#!/usr/bin/env python3
"""Console NetSDK diagnostics for Dahua access controllers."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import sys
import time
from ctypes import POINTER, c_char, c_char_p, c_int, c_long, cast, sizeof
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Callback import CB_FUNCTYPE, fDisConnect, fHaveReConnect
from NetSDK.SDK_Enum import (
    EM_A_NET_ACCESS_CTL_EVENT_TYPE,
    EM_A_NET_ACCESS_CTL_STATUS_TYPE,
    EM_A_NET_ACCESS_DOOROPEN_METHOD,
    EM_LOGIN_SPAC_CAP_TYPE,
    SDK_ALARM_TYPE,
)
from NetSDK.SDK_Struct import (
    C_DWORD,
    C_LDWORD,
    C_LLONG,
    LOG_SET_PRINT_INFO,
    NET_A_ALARM_ACCESS_CTL_EVENT_INFO,
    NET_A_ALARM_ACCESS_CTL_STATUS_INFO,
    NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
    NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
)


DEFAULT_ENV_FILE = Path("secrets/kd1_piwnica_kozla.env")

_APP: "NetSdkConsole | None" = None

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


@dataclass(frozen=True)
class ControllerConfig:
    name: str
    host: str
    port: int
    username: str
    password: str


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Missing env file: {path}")

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env line {line_number} in {path}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


def load_config(path: Path) -> ControllerConfig:
    values = {**load_env_file(path), **os.environ}
    missing = [
        key
        for key in (
            "DAHUA_CONTROLLER_NAME",
            "DAHUA_HOST",
            "DAHUA_PORT",
            "DAHUA_USERNAME",
            "DAHUA_PASSWORD",
        )
        if not values.get(key)
    ]
    if missing:
        raise ValueError(f"Missing required settings: {', '.join(missing)}")

    return ControllerConfig(
        name=values["DAHUA_CONTROLLER_NAME"],
        host=values["DAHUA_HOST"],
        port=int(values["DAHUA_PORT"]),
        username=values["DAHUA_USERNAME"],
        password=values["DAHUA_PASSWORD"],
    )


def decode_bytes(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        raw = value
    elif hasattr(value, "contents"):
        raw = cast(value, c_char_p).value or b""
    else:
        raw = bytes(value)
    raw = raw.split(b"\x00", 1)[0]
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.hex()


def sdk_time_to_string(value: Any) -> str:
    fields = ("dwYear", "dwMonth", "dwDay", "dwHour", "dwMinute", "dwSecond")
    try:
        parts = [int(getattr(value, field)) for field in fields]
        if parts[0] <= 0:
            return ""
        return dt.datetime(*parts).isoformat(sep=" ")
    except (TypeError, ValueError):
        return ""


def enum_name(enum_cls: Any, value: Any) -> str:
    try:
        return enum_cls(value).name
    except Exception:
        return str(int(value)) if isinstance(value, int) else str(value)


def sdk_alarm_name(value: int) -> str:
    for name in dir(SDK_ALARM_TYPE):
        if name.startswith("_"):
            continue
        attr = getattr(SDK_ALARM_TYPE, name)
        if isinstance(attr, int) and int(attr) == int(value):
            return name
    return f"UNKNOWN_{int(value)}"


def simplify_enum_name(name: str, *prefixes: str) -> str:
    value = name
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.lower()


def normalize_open_method(value: int) -> str:
    name = enum_name(EM_A_NET_ACCESS_DOOROPEN_METHOD, value)
    short = simplify_enum_name(name, "NET_ACCESS_DOOROPEN_METHOD_")
    aliases = {
        "card": "card",
        "pwd_only": "pin",
        "remote": "remote",
        "button": "button",
        "fingerprint": "fingerprint",
        "face_recognition": "face",
        "qrcode": "qrcode",
    }
    return aliases.get(short, short)


def normalize_event_type(value: int) -> str:
    name = enum_name(EM_A_NET_ACCESS_CTL_EVENT_TYPE, value)
    return simplify_enum_name(name, "NET_ACCESS_CTL_EVENT_")


def normalize_door_status(value: int) -> str:
    name = enum_name(EM_A_NET_ACCESS_CTL_STATUS_TYPE, value)
    short = simplify_enum_name(name, "NET_ACCESS_CTL_STATUS_TYPE_")
    aliases = {
        "open": "open",
        "close": "closed",
        "abnormal": "abnormal",
        "fakelocked": "fake_locked",
        "closealways": "always_closed",
        "openalways": "always_open",
        "normal": "normal",
        "unknown": "unknown",
    }
    return aliases.get(short, short)


def normalize_access_result(status: int, error_code: int) -> str:
    if int(status) == 1 and int(error_code) == 0:
        return "access_granted"
    if int(status) == 0:
        return "access_denied"
    return "access_unknown"


@CB_FUNCTYPE(None, c_long, C_LLONG, POINTER(c_char), C_DWORD, POINTER(c_char), c_long, c_int, c_long, C_LDWORD)
def message_callback(l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, b_alarm_ack_flag, n_event_id, dw_user):
    if _APP is not None:
        _APP.handle_message(l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, n_event_id)


class NetSdkConsole:
    def __init__(self, config: ControllerConfig, log_sdk: bool = False) -> None:
        self.config = config
        self.log_sdk = log_sdk
        self.login_id = C_LLONG()
        self.is_listening = False
        self.sdk = NetClient()
        self.disconnect_callback = fDisConnect(self.on_disconnect)
        self.reconnect_callback = fHaveReConnect(self.on_reconnect)

    def __enter__(self) -> "NetSdkConsole":
        global _APP
        _APP = self
        self.sdk.InitEx(self.disconnect_callback)
        self.sdk.SetAutoReconnect(self.reconnect_callback)
        self.sdk.SetDVRMessCallBackEx1(message_callback, 0)
        if self.log_sdk:
            self.open_sdk_log()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        global _APP
        _APP = None

    def open_sdk_log(self) -> None:
        log_info = LOG_SET_PRINT_INFO()
        log_info.dwSize = sizeof(LOG_SET_PRINT_INFO)
        log_info.bSetFilePath = 1
        log_info.szLogFilePath = str(Path("sdk") / "netsdk_console.log").encode("gbk")
        self.sdk.LogOpen(log_info)

    def login(self) -> bool:
        in_param = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        in_param.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        in_param.szIP = self.config.host.encode()
        in_param.nPort = self.config.port
        in_param.szUserName = self.config.username.encode()
        in_param.szPassword = self.config.password.encode()
        in_param.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        in_param.pCapParam = None

        out_param = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        out_param.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        self.login_id, device_info, error_msg = self.sdk.LoginWithHighLevelSecurity(in_param, out_param)
        if self.login_id:
            print(f"LOGIN OK: {self.config.name} {self.config.host}:{self.config.port}")
            self.print_device_info(device_info)
            return True

        print(f"LOGIN FAILED: {error_msg}")
        return False

    def print_device_info(self, device_info: Any) -> None:
        print("DEVICE INFO")
        for field in (
            "sSerialNumber",
            "nChanNum",
            "nAlarmInPortNum",
            "nAlarmOutPortNum",
            "nDiskNum",
            "nDVRType",
        ):
            if hasattr(device_info, field):
                value = getattr(device_info, field)
                if field.startswith("s"):
                    value = decode_bytes(value)
                print(f"  {field}: {value}")

    def listen(self) -> int:
        if not self.login_id and not self.login():
            return 2

        result = self.sdk.StartListenEx(self.login_id)
        if not result:
            print(f"START LISTEN FAILED: {self.sdk.GetLastErrorMessage()}")
            return 3

        self.is_listening = True
        print("LISTENING: use card/PIN at the reader now. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\nStopping listener...")
        finally:
            self.sdk.StopListen(self.login_id)
            self.is_listening = False
        return 0

    def handle_message(self, l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, n_event_id) -> None:
        if self.login_id and int(l_login_id) != int(self.login_id):
            return

        timestamp = dt.datetime.now().isoformat(sep=" ", timespec="seconds")
        command = int(l_command)

        if command == int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_EVENT):
            info = cast(p_buf, POINTER(NET_A_ALARM_ACCESS_CTL_EVENT_INFO)).contents
            result = normalize_access_result(info.bStatus, info.nErrorCode)
            event_type = normalize_event_type(info.emEventType)
            method = normalize_open_method(info.emOpenMethod)
            print(
                f"{timestamp} ACCESS_EVENT "
                f"result={result} "
                f"door={info.nDoor} "
                f"event_type={event_type}({info.emEventType}) "
                f"status={info.bStatus} "
                f"open_method={method}({info.emOpenMethod}) "
                f"reader={decode_bytes(info.szReaderID)} "
                f"user_id={decode_bytes(info.szUserID)} "
                f"card={decode_bytes(info.szCardNo)} "
                f"card_name={decode_bytes(info.szCardNameEx) or decode_bytes(info.szCardName)} "
                f"error={info.nErrorCode} "
                f"device_time={sdk_time_to_string(info.stuTime)}"
            )
            return

        if command == int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_STATUS):
            info = cast(p_buf, POINTER(NET_A_ALARM_ACCESS_CTL_STATUS_INFO)).contents
            door_state = normalize_door_status(info.emStatus)
            print(
                f"{timestamp} ACCESS_STATUS "
                f"door={info.nDoor} "
                f"status={door_state}({info.emStatus}) "
                f"serial={decode_bytes(info.szSerialNumber)} "
                f"device_time={sdk_time_to_string(info.stuTime)}"
            )
            return

        print(
            f"{timestamp} RAW_ALARM "
            f"command={command} "
            f"command_name={sdk_alarm_name(command)} "
            f"event_id={n_event_id} "
            f"len={dw_buf_len} "
            f"ip={decode_bytes(pch_dvr_ip)}:{n_dvr_port}"
        )

    def on_disconnect(self, l_login_id, pch_dvr_ip, n_dvr_port, dw_user) -> None:
        print(f"DISCONNECTED: {decode_bytes(pch_dvr_ip)}:{n_dvr_port}")

    def on_reconnect(self, l_login_id, pch_dvr_ip, n_dvr_port, dw_user) -> None:
        print(f"RECONNECTED: {decode_bytes(pch_dvr_ip)}:{n_dvr_port}")

    def close(self) -> None:
        if self.login_id:
            if self.is_listening:
                self.sdk.StopListen(self.login_id)
                self.is_listening = False
            self.sdk.Logout(self.login_id)
            self.login_id = C_LLONG()
        self.sdk.Cleanup()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dahua NetSDK console diagnostics.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--sdk-log", action="store_true", help="Write NetSDK log to sdk/netsdk_console.log.")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("login", help="Login and print basic device info.")
    subparsers.add_parser("listen", help="Login and listen for Dahua alarm/access events.")
    parser.set_defaults(command="login")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    with NetSdkConsole(config, log_sdk=args.sdk_log) as console:
        if args.command == "listen":
            return console.listen()
        return 0 if console.login() else 2


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main(sys.argv[1:]))
