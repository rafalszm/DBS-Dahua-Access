#!/usr/bin/env python3
"""Small NetSDK probe for testing NVR-side event/record triggers."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from ctypes import POINTER, c_char, c_char_p, c_int, c_long, c_uint32, cast, sizeof, string_at, Structure
from pathlib import Path
from typing import Any

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Callback import CB_FUNCTYPE, fDisConnect, fHaveReConnect
from NetSDK.SDK_Enum import CFG_CMD_TYPE, CtrlType, EM_LOGIN_SPAC_CAP_TYPE, SDK_ALARM_TYPE
from NetSDK.SDK_Struct import (
    ALARM_MOTIONDETECT_INFO,
    C_DWORD,
    C_LDWORD,
    C_LLONG,
    NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
    NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def decode_bytes(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        raw = value
    elif hasattr(value, "contents"):
        raw = cast(value, c_char_p).value or b""
    else:
        raw = bytes(value)
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing env: {name}")
    return value


def channel_label(channel: int) -> str:
    label = os.environ.get("DAHUA_NVR_CHANNEL_LABEL")
    if label:
        return label
    channel_id = os.environ.get("DAHUA_NVR_CHANNEL_ID")
    channel_name = os.environ.get("DAHUA_NVR_CHANNEL_NAME")
    if channel_id and channel_name:
        return f"{channel_id} {channel_name}"
    ui_channel = os.environ.get("DAHUA_NVR_CHANNEL_UI")
    if ui_channel:
        return f"UI channel {ui_channel}"
    return f"SDK channel {channel}"


class NotifyA(Structure):
    _fields_ = [
        ("dwSize", c_uint32),
        ("nChannel", c_int),
        ("nAction", c_int),
        ("szEvent", c_char * 64),
    ]


class NotifyB(Structure):
    _fields_ = [
        ("dwSize", c_uint32),
        ("szEvent", c_char * 64),
        ("nChannel", c_int),
        ("nAction", c_int),
    ]


class NotifyC(Structure):
    _fields_ = [
        ("dwSize", c_uint32),
        ("nChannel", c_int),
        ("emEventType", c_int),
        ("nAction", c_int),
        ("szEvent", c_char * 64),
    ]


class OutTiny(Structure):
    _fields_ = [
        ("dwSize", c_uint32),
        ("nResult", c_int),
    ]


def notify_struct(cls: type[Structure], event: str, channel: int, action: int, event_type: int = 0x218F) -> Structure:
    item = cls()
    item.dwSize = sizeof(cls)
    if hasattr(item, "nChannel"):
        item.nChannel = int(channel)
    if hasattr(item, "nAction"):
        item.nAction = int(action)
    if hasattr(item, "emEventType"):
        item.emEventType = int(event_type)
    if hasattr(item, "szEvent"):
        item.szEvent = event.encode("ascii", errors="ignore")[:63]
    return item


def alarm_name(value: int) -> str:
    for name in dir(SDK_ALARM_TYPE):
        if name.startswith("_"):
            continue
        attr = getattr(SDK_ALARM_TYPE, name)
        if isinstance(attr, int) and int(attr) == int(value):
            return name
    return f"UNKNOWN_{int(value)}"


@CB_FUNCTYPE(None, c_long, C_LLONG, POINTER(c_char), C_DWORD, POINTER(c_char), c_long, c_int, c_long, C_LDWORD)
def message_callback(l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, b_alarm_ack_flag, n_event_id, dw_user):
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    command = int(l_command)
    extra = ""
    if command == int(SDK_ALARM_TYPE.MOTION_ALARM_EX):
        raw = string_at(p_buf, int(dw_buf_len))
        active = [index for index, value in enumerate(raw) if value]
        extra = f" active_motion_channels={active}"
    elif command == int(SDK_ALARM_TYPE.ALARM_ALARM_EX):
        raw = string_at(p_buf, int(dw_buf_len))
        active = [index for index, value in enumerate(raw) if value]
        extra = f" active_alarm_inputs={active}"
    elif command == int(SDK_ALARM_TYPE.EVENT_MOTIONDETECT):
        info = cast(p_buf, POINTER(ALARM_MOTIONDETECT_INFO)).contents
        extra = f" channel={int(info.nChannelID)} action={int(info.nEventAction)} event_id={int(info.nEventID)}"
    print(
        f"{timestamp} NVR_CALLBACK command={command} command_name={alarm_name(command)} "
        f"event_id={int(n_event_id)} len={int(dw_buf_len)} "
        f"ip={decode_bytes(pch_dvr_ip)}:{int(n_dvr_port)}{extra}"
    )


def get_config(sdk: NetClient, login_id: int, command: str, channel: int) -> None:
    from ctypes import create_string_buffer

    buffer = create_string_buffer(256 * 1024)
    ok = bool(sdk.GetNewDevConfig(login_id, command, channel, buffer, len(buffer), c_int(0), 3000))
    raw = bytes(buffer).split(b"\x00", 1)[0]
    sample = raw[:220].decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
    print(f"CONFIG {command} channel={channel} ok={ok} len={len(raw)} err={'' if ok else sdk.GetLastErrorMessage()}")
    if sample:
        print(f"  sample={sample}")


def control_once(sdk: NetClient, login_id: int, name: str, ctrl: CtrlType, param: Any) -> bool:
    ok = bool(sdk.ControlDevice(login_id, ctrl, param, 5000))
    print(f"CONTROL {name} ok={ok} err={'' if ok else sdk.GetLastErrorMessage()}")
    return ok


def control_ex_once(sdk: NetClient, login_id: int, name: str, ctrl: CtrlType, in_param: Any) -> bool:
    out_param = OutTiny()
    out_param.dwSize = sizeof(OutTiny)
    ok = bool(sdk.ControlDeviceEx(login_id, ctrl, in_param, out_param, 5000))
    print(f"CONTROL_EX {name} ok={ok} out={out_param.nResult} err={'' if ok else sdk.GetLastErrorMessage()}")
    return ok


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Probe NVR event/record trigger behavior.")
    parser.add_argument("--env-file", type=Path, default=Path("secrets/nvr_10_10_20_20.env"))
    parser.add_argument("--active", action="store_true", help="Send active test commands to the NVR.")
    parser.add_argument("--channel", type=int, help="SDK channel index to test. Dahua SDK usually counts from 0.")
    parser.add_argument("--alarm-input", type=int, help="SDK alarm input index/value to test.")
    parser.add_argument("--wait", type=int, default=8, help="Seconds to wait for NVR callbacks after active tests.")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    host = required("DAHUA_NVR_HOST")
    port = int(required("DAHUA_NVR_PORT"))
    username = required("DAHUA_NVR_USERNAME")
    password = required("DAHUA_NVR_PASSWORD")
    channel = args.channel if args.channel is not None else int(os.environ.get("DAHUA_NVR_CHANNEL", "15"))
    alarm_input = args.alarm_input if args.alarm_input is not None else int(os.environ.get("DAHUA_NVR_ALARM_INPUT", "1"))
    label = channel_label(channel)

    sdk = NetClient()
    disconnect_callback = fDisConnect(lambda *cb_args: print("NVR_DISCONNECT"))
    reconnect_callback = fHaveReConnect(lambda *cb_args: print("NVR_RECONNECT"))
    sdk.InitEx(disconnect_callback)
    sdk.SetAutoReconnect(reconnect_callback)
    sdk.SetDVRMessCallBackEx1(message_callback, 0)

    in_param = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
    in_param.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
    in_param.szIP = host.encode()
    in_param.nPort = port
    in_param.szUserName = username.encode()
    in_param.szPassword = password.encode()
    in_param.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
    in_param.pCapParam = None

    out_param = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
    out_param.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

    login, device_info, error = sdk.LoginWithHighLevelSecurity(in_param, out_param)
    login_id = int(login.value) if hasattr(login, "value") else int(login)
    if not login_id:
        print(f"LOGIN FAILED {host}:{port}: {error}")
        sdk.Cleanup()
        return 2

    print(f"LOGIN OK {host}:{port} channel={channel} label={label} alarm_input={alarm_input}")
    print(
        "DEVICE "
        f"serial={decode_bytes(device_info.sSerialNumber)} "
        f"channels={int(device_info.nChanNum)} "
        f"alarm_in={int(device_info.nAlarmInPortNum)} "
        f"alarm_out={int(device_info.nAlarmOutPortNum)} "
        f"type={int(device_info.nDVRType)}"
    )

    listen_ok = bool(sdk.StartListenEx(login_id))
    print(f"LISTEN {listen_ok} err={'' if listen_ok else sdk.GetLastErrorMessage()}")

    for command in (CFG_CMD_TYPE.RECORD, CFG_CMD_TYPE.RECORDMODE, CFG_CMD_TYPE.MOTIONDETECT, CFG_CMD_TYPE.SNAP):
        get_config(sdk, login_id, command, channel)

    if args.active:
        print("ACTIVE TESTS START")
        control_once(sdk, login_id, f"CAPTURE_START channel={channel}", CtrlType.CAPTURE_START, c_int(channel))
        control_once(sdk, login_id, f"MARK_IMPORTANT_RECORD channel={channel}", CtrlType.MARK_IMPORTANT_RECORD, c_int(channel))
        control_once(sdk, login_id, f"TRIGGER_ALARM_IN input={alarm_input}", CtrlType.TRIGGER_ALARM_IN, c_int(alarm_input))

        for event in ("MotionDetect", "Alarm", "ManualSnap"):
            for cls in (NotifyA, NotifyB, NotifyC):
                for action, label in ((0, "pulse"), (1, "start")):
                    item = notify_struct(cls, event, channel, action)
                    control_ex_once(sdk, login_id, f"NOTIFY_EVENT {cls.__name__}:{event}:{label}", CtrlType.NOTIFY_EVENT, item)
                    time.sleep(0.2)
        print(f"WAIT {args.wait}s for callbacks")
        time.sleep(max(0, args.wait))

    if listen_ok:
        sdk.StopListen(login_id)
    sdk.Logout(login_id)
    sdk.Cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
