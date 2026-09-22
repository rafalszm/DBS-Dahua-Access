#!/usr/bin/env python3
"""Probe Dahua access-controller names: doors, subcontrollers, readers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from ctypes import c_int, create_string_buffer, sizeof
from pathlib import Path
from typing import Any

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Enum import EM_A_NET_EM_ACCESS_CTL_MANAGER, EM_LOGIN_SPAC_CAP_TYPE, EM_QUERY_DEV_STATE_TYPE
from NetSDK.SDK_Struct import (
    NET_A_DEV_VERSION_INFO,
    NET_IN_GET_SUB_CONTROLLER_INFO,
    NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
    NET_OUT_GET_SUB_CONTROLLER_INFO,
    NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


CONFIG_CANDIDATES = (
    "AccessEvent",
    "AccessControl",
    "AccessControlGeneral",
    "AccessControlDoor",
    "AccessDoor",
    "Door",
    "AccessControlChannel",
    "AccessTimeSchedule",
    "AccessControlRepeatEnterRoute",
    "AccessControlABLock",
    "ChannelTitle",
    "General",
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing env: {name}")
    return value


def decode_bytes(value: Any) -> str:
    if value is None:
        return ""
    raw = bytes(value).split(b"\x00", 1)[0]
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.hex()


def decode_reader_ids(raw: Any, count: int) -> list[str]:
    data = bytes(raw)
    readers: list[str] = []
    for index in range(max(0, min(count, 32))):
        chunk = data[index * 32 : (index + 1) * 32]
        value = chunk.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()
        if value:
            readers.append(value)
    return readers


def compact_json_sample(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:500].replace("\r", " ").replace("\n", " ")
    return json.dumps(parsed, ensure_ascii=False)[:700]


def login(sdk: NetClient) -> tuple[int, Any]:
    host = required("DAHUA_HOST")
    port = int(required("DAHUA_PORT"))
    username = required("DAHUA_USERNAME")
    password = required("DAHUA_PASSWORD")

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
    login_id, device_info, error_msg = sdk.LoginWithHighLevelSecurity(in_param, out_param)
    raw_login_id = int(login_id.value) if hasattr(login_id, "value") else int(login_id)
    if not raw_login_id:
        raise SystemExit(f"LOGIN FAILED {host}:{port}: {error_msg}")
    print(f"LOGIN OK {host}:{port}")
    print(
        "DEVICE "
        f"serial={decode_bytes(device_info.sSerialNumber)} "
        f"channels={int(device_info.nChanNum)} "
        f"alarm_in={int(device_info.nAlarmInPortNum)} "
        f"alarm_out={int(device_info.nAlarmOutPortNum)} "
        f"type={int(device_info.nDVRType)}"
    )
    return raw_login_id, device_info


def probe_subcontrollers(sdk: NetClient, login_id: int) -> None:
    in_param = NET_IN_GET_SUB_CONTROLLER_INFO()
    in_param.dwSize = sizeof(NET_IN_GET_SUB_CONTROLLER_INFO)
    in_param.nSubControllerID[0] = -1
    in_param.nSubControllerNum = 1

    out_param = NET_OUT_GET_SUB_CONTROLLER_INFO()
    out_param.dwSize = sizeof(NET_OUT_GET_SUB_CONTROLLER_INFO)

    ok = bool(
        sdk.OperateAccessControlManager(
            login_id,
            EM_A_NET_EM_ACCESS_CTL_MANAGER.NET_EM_ACCESS_CTL_GETSUBCONTROLLER_INFO,
            in_param,
            out_param,
            5000,
        )
    )
    print(f"ACCESS_MANAGER GETSUBCONTROLLER_INFO ok={ok} err={'' if ok else sdk.GetLastErrorMessage()}")
    if not ok:
        return
    print(f"  returned={int(out_param.nRetNum)}")
    for index in range(int(out_param.nRetNum)):
        item = out_param.stuSubControllerInfo[index]
        print(
            "  SUB_CONTROLLER "
            f"id={int(item.nSubControllerID)} "
            f"name={decode_bytes(item.szSubControllerName)!r} "
            f"property={int(item.emProperty)} "
            f"type={decode_bytes(item.szDeviceType)!r} "
            f"version={decode_bytes(item.szVesion)!r} "
            f"door_count={int(item.nDoorNum)}"
        )
        for reader_index in range(min(max(0, int(item.nDoorNum)), 128)):
            reader = item.stuReaderInfo[reader_index]
            door = int(reader.nDoor)
            read_num = int(reader.nReadNum)
            readers = decode_reader_ids(reader.szReadID, read_num)
            if door or read_num or readers:
                print(f"    DOOR_READER door={door} reader_count={read_num} reader_ids={readers}")


def probe_software_info(sdk: NetClient, login_id: int) -> None:
    info = NET_A_DEV_VERSION_INFO()
    ok = bool(
        sdk.QueryDevState(
            login_id,
            EM_QUERY_DEV_STATE_TYPE.SOFTWARE,
            info,
            sizeof(info),
            0,
            5000,
        )
    )
    print(f"SOFTWARE_INFO ok={ok} err={'' if ok else sdk.GetLastErrorMessage()}")
    if ok:
        print(
            "  PRODUCT "
            f"type={decode_bytes(info.szDevType)!r} "
            f"detail_type={decode_bytes(info.szDetailType)!r} "
            f"firmware={decode_bytes(info.szSoftWareVersion)!r} "
            f"hardware={decode_bytes(info.szHardwareVersion)!r}"
        )


def probe_new_configs(sdk: NetClient, login_id: int, max_channel: int) -> None:
    channels = [-1, *range(0, max_channel + 1)]
    for command in CONFIG_CANDIDATES:
        for channel in channels:
            buffer = create_string_buffer(512 * 1024)
            error = c_int(0)
            ok = bool(sdk.GetNewDevConfig(login_id, command, channel, buffer, len(buffer), error, 3000))
            raw = bytes(buffer).split(b"\x00", 1)[0]
            text = raw.decode("utf-8", errors="replace").strip()
            if ok or text:
                print(
                    f"CONFIG {command} channel={channel} ok={ok} "
                    f"len={len(raw)} err={'' if ok else sdk.GetLastErrorMessage()}"
                )
                if text:
                    print(f"  sample={compact_json_sample(text)}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Probe access-controller door and passage names.")
    parser.add_argument("--env-file", type=Path, default=Path("secrets/kd1_piwnica_kozla.env"))
    parser.add_argument("--max-channel", type=int, default=4)
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    sdk = NetClient()
    sdk.InitEx(None)
    login_id = 0
    try:
        login_id, _device_info = login(sdk)
        probe_software_info(sdk, login_id)
        probe_subcontrollers(sdk, login_id)
        probe_new_configs(sdk, login_id, args.max_channel)
    finally:
        if login_id:
            sdk.Logout(login_id)
        sdk.Cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
