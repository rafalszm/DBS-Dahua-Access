#!/usr/bin/env python3
"""Local web dashboard for Dahua NetSDK lab tests."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import sys
import threading
import time
import webbrowser
from ctypes import POINTER, c_char, c_char_p, c_int, c_long, cast, create_string_buffer, sizeof, string_at
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Callback import CB_FUNCTYPE, fDisConnect, fHaveReConnect
from NetSDK.SDK_Enum import (
    CFG_CMD_TYPE,
    CtrlType,
    EM_A_NET_EM_ACCESS_CTL_USER_SERVICE,
    EM_A_NET_ACCESS_CTL_EVENT_TYPE,
    EM_A_NET_ACCESS_CTL_STATUS_TYPE,
    EM_A_NET_ACCESS_DOOROPEN_METHOD,
    EM_LOGIN_SPAC_CAP_TYPE,
    EM_OPEN_DOOR_DIRECTION,
    EM_OPEN_DOOR_TYPE,
    SDK_ALARM_TYPE,
)
from NetSDK.SDK_Struct import (
    C_ENUM,
    C_DWORD,
    C_LDWORD,
    C_LLONG,
    NET_ACCESS_USER_INFO,
    ALARM_MOTIONDETECT_INFO,
    NET_A_ALARM_ACCESS_CTL_EVENT_INFO,
    NET_A_ALARM_ACCESS_CTL_STATUS_INFO,
    NET_CTRL_ACCESS_OPEN,
    NET_IN_MANUAL_SNAP,
    NET_IN_ACCESS_USER_SERVICE_GET,
    NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
    NET_OUT_MANUAL_SNAP,
    NET_OUT_ACCESS_USER_SERVICE_GET,
    NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


_APP: "DahuaWebController | None" = None


@dataclass(frozen=True)
class ControllerConfig:
    name: str
    host: str
    port: int
    username: str
    password: str
    default_door: int = 2
    doors: tuple[int, ...] = (1, 2, 3, 4)
    door_names: dict[int, str] = field(default_factory=dict)
    default_reader: str = ""


@dataclass(frozen=True)
class CameraConfig:
    name: str
    host: str
    port: int
    username: str
    password: str
    channel: int = 0


@dataclass(frozen=True)
class NvrConfig:
    name: str
    host: str
    port: int
    username: str
    password: str
    channel: int = 14
    channel_ui: int = 15
    channel_id: str = "D15"
    channel_name: str = "MAGAZYN"
    channel_label: str = "D15 MAGAZYN"
    alarm_input: int = 1


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def parse_door_list(value: str) -> tuple[int, ...]:
    doors: list[int] = []
    for chunk in value.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            doors.extend(range(int(start), int(end) + 1))
            continue
        doors.append(int(chunk))
    unique = sorted({door for door in doors if door > 0})
    return tuple(unique or (1, 2, 3, 4))


def parse_door_names(doors: tuple[int, ...]) -> dict[int, str]:
    names: dict[int, str] = {}
    raw = os.environ.get("DAHUA_DOOR_NAMES", "")
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        separator = "=" if "=" in chunk else ":" if ":" in chunk else ""
        if not separator:
            continue
        key, value = chunk.split(separator, 1)
        try:
            door = int(key.strip())
        except ValueError:
            continue
        value = value.strip()
        if value:
            names[door] = value
    for door in doors:
        value = os.environ.get(f"DAHUA_DOOR_{door}_NAME", "").strip()
        if value:
            names[door] = value
    return names


def load_config() -> ControllerConfig:
    missing = [
        key
        for key in ("DAHUA_CONTROLLER_NAME", "DAHUA_HOST", "DAHUA_PORT", "DAHUA_USERNAME", "DAHUA_PASSWORD")
        if not os.environ.get(key)
    ]
    if missing:
        raise ValueError(f"Missing env values: {', '.join(missing)}")

    doors = parse_door_list(os.environ.get("DAHUA_DOORS", "1,2,3,4"))
    return ControllerConfig(
        name=os.environ["DAHUA_CONTROLLER_NAME"],
        host=os.environ["DAHUA_HOST"],
        port=int(os.environ["DAHUA_PORT"]),
        username=os.environ["DAHUA_USERNAME"],
        password=os.environ["DAHUA_PASSWORD"],
        default_door=int(os.environ.get("DAHUA_DEFAULT_DOOR", "2")),
        doors=doors,
        door_names=parse_door_names(doors),
        default_reader=os.environ.get("DAHUA_DEFAULT_READER", ""),
    )


def load_camera_config() -> CameraConfig | None:
    if not os.environ.get("DAHUA_CAMERA_HOST"):
        return None
    missing = [
        key
        for key in ("DAHUA_CAMERA_HOST", "DAHUA_CAMERA_PORT", "DAHUA_CAMERA_USERNAME", "DAHUA_CAMERA_PASSWORD")
        if not os.environ.get(key)
    ]
    if missing:
        raise ValueError(f"Missing camera env values: {', '.join(missing)}")
    return CameraConfig(
        name=os.environ.get("DAHUA_CAMERA_NAME", "Dahua camera lab"),
        host=os.environ["DAHUA_CAMERA_HOST"],
        port=int(os.environ["DAHUA_CAMERA_PORT"]),
        username=os.environ["DAHUA_CAMERA_USERNAME"],
        password=os.environ["DAHUA_CAMERA_PASSWORD"],
        channel=int(os.environ.get("DAHUA_CAMERA_CHANNEL", "0")),
    )


def load_nvr_config() -> NvrConfig | None:
    if not os.environ.get("DAHUA_NVR_HOST"):
        return None
    missing = [
        key
        for key in ("DAHUA_NVR_HOST", "DAHUA_NVR_PORT", "DAHUA_NVR_USERNAME", "DAHUA_NVR_PASSWORD")
        if not os.environ.get(key)
    ]
    if missing:
        raise ValueError(f"Missing NVR env values: {', '.join(missing)}")
    channel = int(os.environ.get("DAHUA_NVR_CHANNEL", "14"))
    channel_ui = int(os.environ.get("DAHUA_NVR_CHANNEL_UI", str(channel + 1)))
    channel_id = os.environ.get("DAHUA_NVR_CHANNEL_ID", f"D{channel_ui}")
    channel_name = os.environ.get("DAHUA_NVR_CHANNEL_NAME", "")
    channel_label = os.environ.get("DAHUA_NVR_CHANNEL_LABEL") or " ".join(
        value for value in (channel_id, channel_name) if value
    )
    return NvrConfig(
        name=os.environ.get("DAHUA_NVR_NAME", "Dahua NVR lab"),
        host=os.environ["DAHUA_NVR_HOST"],
        port=int(os.environ["DAHUA_NVR_PORT"]),
        username=os.environ["DAHUA_NVR_USERNAME"],
        password=os.environ["DAHUA_NVR_PASSWORD"],
        channel=channel,
        channel_ui=channel_ui,
        channel_id=channel_id,
        channel_name=channel_name,
        channel_label=channel_label or f"SDK channel {channel}",
        alarm_input=int(os.environ.get("DAHUA_NVR_ALARM_INPUT", "1")),
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
        return dt.datetime(*parts).isoformat()
    except (TypeError, ValueError):
        return ""


def enum_name(enum_cls: Any, value: Any) -> str:
    try:
        return enum_cls(value).name
    except Exception:
        return str(int(value)) if isinstance(value, int) else str(value)


def simplify_enum_name(name: str, *prefixes: str) -> str:
    value = name
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.lower()


def normalize_open_method(value: int) -> str:
    name = enum_name(EM_A_NET_ACCESS_DOOROPEN_METHOD, value)
    short = simplify_enum_name(name, "NET_ACCESS_DOOROPEN_METHOD_")
    return {
        "card": "card",
        "pwd_only": "pin",
        "remote": "remote",
        "button": "button",
        "fingerprint": "fingerprint",
        "face_recognition": "face",
        "qrcode": "qrcode",
    }.get(short, short)


def normalize_event_type(value: int) -> str:
    return simplify_enum_name(enum_name(EM_A_NET_ACCESS_CTL_EVENT_TYPE, value), "NET_ACCESS_CTL_EVENT_")


def normalize_door_status(value: int) -> str:
    short = simplify_enum_name(enum_name(EM_A_NET_ACCESS_CTL_STATUS_TYPE, value), "NET_ACCESS_CTL_STATUS_TYPE_")
    return {
        "open": "open",
        "close": "closed",
        "abnormal": "abnormal",
        "fakelocked": "fake_locked",
        "closealways": "always_closed",
        "openalways": "always_open",
        "normal": "normal",
        "unknown": "unknown",
    }.get(short, short)


def normalize_access_result(status: int, error_code: int) -> str:
    if int(status) == 1 and int(error_code) == 0:
        return "access_granted"
    if int(status) == 0:
        return "access_denied"
    return "access_unknown"


def sdk_alarm_name(value: int) -> str:
    for name in dir(SDK_ALARM_TYPE):
        if name.startswith("_"):
            continue
        attr = getattr(SDK_ALARM_TYPE, name)
        if isinstance(attr, int) and int(attr) == int(value):
            return name
    return f"UNKNOWN_{int(value)}"


@CB_FUNCTYPE(None, c_long, C_LLONG, POINTER(c_char), C_DWORD, POINTER(c_char), c_long, c_int, c_long, C_LDWORD)
def message_callback(l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, b_alarm_ack_flag, n_event_id, dw_user):
    if _APP is not None:
        _APP.handle_message(l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, n_event_id)


CAMERA_ACTIONS: dict[str, dict[str, Any]] = {
    "manual_snap": {"label": "ManualSnap", "capability": "snapshot"},
    "capture_start": {"label": "CAPTURE_START", "control": CtrlType.CAPTURE_START, "capability": "snapshot"},
    "trigger_alarm_in": {"label": "TRIGGER_ALARM_IN", "control": CtrlType.TRIGGER_ALARM_IN, "capability": "alarm_in"},
    "trigger_alarm_out": {"label": "TRIGGER_ALARM_OUT", "control": CtrlType.TRIGGER_ALARM_OUT, "capability": "alarm_out"},
    "trigger_alarm_wireless": {"label": "TRIGGER_ALARM_WIRELESS", "control": CtrlType.TRIGGER_ALARM_WIRELESS, "capability": "wireless_alarm"},
    "mark_important_record": {"label": "MARK_IMPORTANT_RECORD", "control": CtrlType.MARK_IMPORTANT_RECORD, "capability": "record"},
}

NVR_ACTIONS: dict[str, dict[str, Any]] = {
    "mark_important_record": {
        "label": "Oznacz ważne nagranie",
        "control": CtrlType.MARK_IMPORTANT_RECORD,
        "param": "channel",
        "class": "btn-primary",
    },
    "trigger_alarm_in": {
        "label": "Trigger alarm input",
        "control": CtrlType.TRIGGER_ALARM_IN,
        "param": "alarm_input",
        "class": "btn-warning",
    },
    "capture_start": {
        "label": "CAPTURE_START test",
        "control": CtrlType.CAPTURE_START,
        "param": "channel",
        "class": "btn-outline-secondary",
    },
}


class DahuaWebController:
    def __init__(
        self,
        config: ControllerConfig,
        camera_config: CameraConfig | None = None,
        nvr_config: NvrConfig | None = None,
    ) -> None:
        self.config = config
        self.camera_config = camera_config
        self.nvr_config = nvr_config
        self.lock = threading.RLock()
        self.sdk = NetClient()
        self.login_id = C_LLONG()
        self.camera_login_id = C_LLONG()
        self.nvr_login_id = C_LLONG()
        self.is_initialized = False
        self.is_listening = False
        self.is_online = False
        self.camera_online = False
        self.nvr_online = False
        self.nvr_listening = False
        self.device_info: dict[str, Any] = {}
        self.camera_info: dict[str, Any] = {}
        self.nvr_info: dict[str, Any] = {}
        self.last_error = ""
        self.camera_last_error = ""
        self.nvr_last_error = ""
        self.events: list[dict[str, Any]] = []
        self.camera_events: list[dict[str, Any]] = []
        self.nvr_events: list[dict[str, Any]] = []
        self.camera_active_pulses: dict[str, dict[str, Any]] = {}
        self.camera_capabilities: dict[str, Any] = self._empty_camera_capabilities()
        self.nvr_configs: dict[str, Any] = {}
        self.users_by_id: dict[str, dict[str, Any]] = {}
        self.user_lookup_inflight: set[str] = set()
        self.next_event_id = 1
        self.next_camera_event_id = 1
        self.next_nvr_event_id = 1
        self.disconnect_callback = fDisConnect(self.on_disconnect)
        self.reconnect_callback = fHaveReConnect(self.on_reconnect)

    def start(self) -> None:
        global _APP
        _APP = self
        self.sdk.InitEx(self.disconnect_callback)
        self.sdk.SetAutoReconnect(self.reconnect_callback)
        self.sdk.SetDVRMessCallBackEx1(message_callback, 0)
        self.is_initialized = True
        self.add_event("system", {"message": "NetSDK initialized"})

    def stop(self) -> None:
        with self.lock:
            if self.is_listening and self.login_id:
                self.sdk.StopListen(self.login_id)
            self.is_listening = False
            if self.login_id:
                self.sdk.Logout(self.login_id)
            if self.camera_login_id:
                self.sdk.Logout(self.camera_login_id)
            if self.nvr_listening and self.nvr_login_id:
                self.sdk.StopListen(self.nvr_login_id)
            self.nvr_listening = False
            if self.nvr_login_id:
                self.sdk.Logout(self.nvr_login_id)
            self.login_id = C_LLONG()
            self.camera_login_id = C_LLONG()
            self.nvr_login_id = C_LLONG()
            self.is_online = False
            self.camera_online = False
            self.nvr_online = False
            self.sdk.Cleanup()
            self.is_initialized = False

    def ensure_login(self) -> bool:
        with self.lock:
            if self.login_id:
                return True

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

            login_id, device_info, error_msg = self.sdk.LoginWithHighLevelSecurity(in_param, out_param)
            if not login_id:
                self.last_error = str(error_msg)
                self.add_event("system", {"level": "error", "message": f"Login failed: {error_msg}"})
                return False

            self.login_id = login_id
            self.is_online = True
            self.last_error = ""
            self.device_info = self._device_info_to_dict(device_info)
            self.add_event("system", {"message": "Login OK", "device": self.device_info})
            return True

    def _device_info_to_dict(self, device_info: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for field in ("sSerialNumber", "nChanNum", "nAlarmInPortNum", "nAlarmOutPortNum", "nDiskNum", "nDVRType"):
            if hasattr(device_info, field):
                value = getattr(device_info, field)
                data[field] = decode_bytes(value) if field.startswith("s") else int(value)
        return data

    def start_listen(self) -> bool:
        with self.lock:
            if self.is_listening:
                return True
            if not self.ensure_login():
                return False
            result = self.sdk.StartListenEx(self.login_id)
            if not result:
                self.last_error = self.sdk.GetLastErrorMessage()
                self.add_event("system", {"level": "error", "message": f"Start listen failed: {self.last_error}"})
                return False
            self.is_listening = True
            self.add_event("system", {"message": "Listening started"})
            return True

    def connect_and_listen(self) -> bool:
        return self.start_listen()

    def stop_listen(self) -> bool:
        with self.lock:
            if self.is_listening and self.login_id:
                self.sdk.StopListen(self.login_id)
            self.is_listening = False
            self.add_event("system", {"message": "Listening stopped"})
            return True

    def disconnect(self) -> bool:
        with self.lock:
            if self.is_listening and self.login_id:
                self.sdk.StopListen(self.login_id)
            self.is_listening = False
            if self.login_id:
                self.sdk.Logout(self.login_id)
            self.login_id = C_LLONG()
            self.is_online = False
            self.add_event("system", {"message": "Logged out"})
            return True

    def ensure_camera_login(self) -> bool:
        with self.lock:
            if self.camera_login_id:
                return True
            if self.camera_config is None:
                self.camera_last_error = "Camera is not configured"
                self.add_camera_event("camera_error", {"error": self.camera_last_error})
                return False

            in_param = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
            in_param.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
            in_param.szIP = self.camera_config.host.encode()
            in_param.nPort = self.camera_config.port
            in_param.szUserName = self.camera_config.username.encode()
            in_param.szPassword = self.camera_config.password.encode()
            in_param.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
            in_param.pCapParam = None

            out_param = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
            out_param.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

            login_id, device_info, error_msg = self.sdk.LoginWithHighLevelSecurity(in_param, out_param)
            if not login_id:
                self.camera_last_error = str(error_msg)
                self.add_camera_event("camera_login_failed", {"error": self.camera_last_error})
                return False

            self.camera_login_id = login_id
            self.camera_online = True
            self.camera_last_error = ""
            self.camera_info = self._device_info_to_dict(device_info)
            self.add_camera_event("camera_login_ok", {"device": self.camera_info})
            self.refresh_camera_capabilities(self.camera_config.channel, test_actions=False)
            return True

    def disconnect_camera(self) -> bool:
        with self.lock:
            self.camera_active_pulses.clear()
            if self.camera_login_id:
                self.sdk.Logout(self.camera_login_id)
            self.camera_login_id = C_LLONG()
            self.camera_online = False
            self.camera_capabilities = self._empty_camera_capabilities()
            self.add_camera_event("camera_logged_out", {})
            return True

    def _empty_camera_capabilities(self) -> dict[str, Any]:
        return {
            "checked_at": "",
            "channel": None,
            "features": {},
            "configs": {},
            "actions": {
                key: {
                    "label": value["label"],
                    "supported": False,
                    "available": False,
                    "source": "not_checked",
                    "last_ok": None,
                    "last_error": "",
                }
                for key, value in CAMERA_ACTIONS.items()
            },
        }

    def refresh_camera_capabilities(self, channel: int | None = None, test_actions: bool = False) -> dict[str, Any]:
        if self.camera_config is None:
            return {"ok": False, "error": "Camera is not configured"}
        channel = self.camera_config.channel if channel is None else int(channel)
        if not self.ensure_camera_login():
            return {"ok": False, "error": self.camera_last_error}

        with self.lock:
            login_id = int(self.camera_login_id.value) if hasattr(self.camera_login_id, "value") else int(self.camera_login_id)
            device_info = dict(self.camera_info)
            previous_actions = dict(self.camera_capabilities.get("actions", {}))

        configs = self._probe_camera_configs(login_id, channel)
        features = self._camera_features_from_probe(device_info, configs, channel)
        actions = self._camera_actions_from_features(features, previous_actions)

        with self.lock:
            self.camera_capabilities = {
                "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
                "channel": channel,
                "features": features,
                "configs": configs,
                "actions": actions,
            }

        if test_actions:
            for action in list(CAMERA_ACTIONS):
                self._run_camera_action_once(action, channel)
            self.refresh_camera_capabilities(channel, test_actions=False)

        self.add_camera_event(
            "camera_capabilities",
            {
                "channel": channel,
                "available_actions": [
                    key for key, value in self.camera_capabilities["actions"].items() if value.get("available")
                ],
            },
        )
        return {"ok": True, "capabilities": self.camera_capabilities}

    def _probe_camera_configs(self, login_id: int, channel: int) -> dict[str, Any]:
        commands = {
            "motion_detect": CFG_CMD_TYPE.MOTIONDETECT,
            "snap": CFG_CMD_TYPE.SNAP,
            "record": CFG_CMD_TYPE.RECORD,
            "record_mode": CFG_CMD_TYPE.RECORDMODE,
            "alarm_out": CFG_CMD_TYPE.ALARMOUT,
            "alarm_in": CFG_CMD_TYPE.ALARMINPUT,
        }
        configs: dict[str, Any] = {}
        for name, command in commands.items():
            buffer = create_string_buffer(512 * 1024)
            ok = bool(self.sdk.GetNewDevConfig(login_id, command, channel, buffer, len(buffer), c_int(0), 3000))
            raw = bytes(buffer).split(b"\x00", 1)[0]
            text = raw.decode("utf-8", errors="replace").strip()
            parsed: Any = None
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            configs[name] = {
                "ok": ok,
                "length": len(raw),
                "result": parsed.get("result") if isinstance(parsed, dict) else None,
                "sample": text[:180],
                "error": "" if ok else self.sdk.GetLastErrorMessage(),
                "parsed": parsed,
            }
        return configs

    def _camera_features_from_probe(self, device_info: dict[str, Any], configs: dict[str, Any], channel: int) -> dict[str, Any]:
        motion_handler = self._config_table(configs.get("motion_detect", {})).get("EventHandler", {})
        channel_count = int(device_info.get("nChanNum") or 0)
        alarm_in_ports = int(device_info.get("nAlarmInPortNum") or 0)
        alarm_out_ports = int(device_info.get("nAlarmOutPortNum") or 0)
        return {
            "channel_exists": channel_count == 0 or channel < channel_count,
            "snapshot_config": bool(configs.get("snap", {}).get("ok")),
            "record_config": bool(configs.get("record", {}).get("ok") or configs.get("record_mode", {}).get("ok")),
            "motion_detect_config": bool(configs.get("motion_detect", {}).get("ok")),
            "motion_records": bool(motion_handler.get("RecordEnable")),
            "motion_snapshots": bool(motion_handler.get("SnapshotEnable")),
            "alarm_in_ports": alarm_in_ports,
            "alarm_out_ports": alarm_out_ports,
            "alarm_in_config": bool(configs.get("alarm_in", {}).get("ok")),
            "alarm_out_config": bool(configs.get("alarm_out", {}).get("ok")),
            "wireless_alarm": False,
        }

    def _config_table(self, config: dict[str, Any]) -> dict[str, Any]:
        parsed = config.get("parsed")
        if not isinstance(parsed, dict):
            return {}
        params = parsed.get("params")
        if not isinstance(params, dict):
            return {}
        table = params.get("table")
        return table if isinstance(table, dict) else {}

    def _camera_actions_from_features(
        self, features: dict[str, Any], previous_actions: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        actions: dict[str, dict[str, Any]] = {}
        for key, meta in CAMERA_ACTIONS.items():
            previous = previous_actions.get(key, {})
            last_ok = previous.get("last_ok")
            last_error = previous.get("last_error", "")
            supported, source = self._camera_action_supported(key, meta["capability"], features, last_ok)
            actions[key] = {
                "label": meta["label"],
                "supported": supported,
                "available": supported,
                "source": source,
                "last_ok": last_ok,
                "last_error": last_error,
            }
        return actions

    def _camera_action_supported(self, action: str, capability: str, features: dict[str, Any], last_ok: Any) -> tuple[bool, str]:
        if last_ok is True:
            return True, "last_action_ok"
        if last_ok is False:
            return False, "last_action_failed"
        if capability == "snapshot":
            supported = bool(features.get("channel_exists") and features.get("snapshot_config") and action == "manual_snap")
            if supported:
                return True, "snap_config"
            if features.get("snapshot_config"):
                return False, "needs_active_test"
            return False, "snap_config_missing"
        if capability == "record":
            if features.get("channel_exists") and features.get("record_config"):
                return False, "needs_active_test"
            return False, "record_config_missing"
        if capability == "alarm_in":
            supported = bool(features.get("alarm_in_ports") or features.get("alarm_in_config"))
            return supported, "alarm_in_capability" if supported else "no_alarm_in_capability"
        if capability == "alarm_out":
            supported = bool(features.get("alarm_out_ports") or features.get("alarm_out_config"))
            return supported, "alarm_out_capability" if supported else "no_alarm_out_capability"
        if capability == "wireless_alarm":
            supported = bool(features.get("wireless_alarm"))
            return supported, "wireless_alarm_capability" if supported else "no_wireless_alarm_capability"
        return False, f"unknown_capability:{action}"

    def run_camera_action(self, action: str, channel: int | None = None) -> dict[str, Any]:
        if self.camera_config is None:
            return {"ok": False, "error": "Camera is not configured"}
        channel = self.camera_config.channel if channel is None else int(channel)
        if not self.ensure_camera_login():
            return {"ok": False, "error": self.camera_last_error}
        if action not in CAMERA_ACTIONS:
            return {"ok": False, "error": f"Unsupported camera action: {action}"}

        ok = self._run_camera_action_once(action, channel)
        return {"ok": ok, "error": "" if ok else self.camera_last_error}

    def _run_camera_action_once(self, action: str, channel: int) -> bool:
        try:
            with self.lock:
                login_id = int(self.camera_login_id.value) if hasattr(self.camera_login_id, "value") else int(self.camera_login_id)
            if not login_id:
                self.camera_last_error = "Camera is not connected"
                self.add_camera_event("camera_action_skipped", {"action": action, "channel": channel, "error": self.camera_last_error})
                return False

            if action == "manual_snap":
                return self._camera_manual_snap(login_id, channel)

            control_type = CAMERA_ACTIONS[action]["control"]
            param = c_int(int(channel))
            result = self.sdk.ControlDevice(login_id, control_type, param, 5000)
            ok = bool(result)
            if ok:
                self.camera_last_error = ""
                self._remember_camera_action_result(action, True, "")
                self.add_camera_event("camera_action_ok", {"action": action, "channel": channel})
            else:
                self.camera_last_error = self.sdk.GetLastErrorMessage()
                self._remember_camera_action_result(action, False, self.camera_last_error)
                self.add_camera_event("camera_action_failed", {"action": action, "channel": channel, "error": self.camera_last_error})
            return ok
        except Exception as exc:
            self.camera_last_error = str(exc)
            self._remember_camera_action_result(action, False, self.camera_last_error)
            self.add_camera_event("camera_action_failed", {"action": action, "channel": channel, "error": self.camera_last_error})
            return False

    def _camera_manual_snap(self, login_id: int, channel: int) -> bool:
        out_dir = Path("lab_captures")
        out_dir.mkdir(exist_ok=True)
        serial = int(time.time() * 1000) % 100000000
        in_param = NET_IN_MANUAL_SNAP()
        in_param.dwSize = sizeof(NET_IN_MANUAL_SNAP)
        in_param.nChannel = int(channel)
        in_param.nCmdSerial = serial

        buffer = create_string_buffer(2 * 1024 * 1024)
        out_param = NET_OUT_MANUAL_SNAP()
        out_param.dwSize = sizeof(NET_OUT_MANUAL_SNAP)
        out_param.nMaxBufLen = len(buffer)
        out_param.pRcvBuf = cast(buffer, POINTER(c_char))

        result = self.sdk.ManualSnap(login_id, in_param, out_param, 5000)
        ok = bool(result)
        if ok:
            size = int(out_param.nRetBufLen)
            filename = ""
            if size > 0:
                filename = str(out_dir / f"camera_ch{channel}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg")
                Path(filename).write_bytes(buffer.raw[:size])
            self.camera_last_error = ""
            self._remember_camera_action_result("manual_snap", True, "")
            self.add_camera_event("camera_manual_snap_ok", {"action": "manual_snap", "channel": channel, "bytes": size, "file": filename})
        else:
            self.camera_last_error = self.sdk.GetLastErrorMessage()
            self._remember_camera_action_result("manual_snap", False, self.camera_last_error)
            self.add_camera_event("camera_manual_snap_failed", {"action": "manual_snap", "channel": channel, "error": self.camera_last_error})
        return ok

    def _remember_camera_action_result(self, action: str, ok: bool, error: str) -> None:
        with self.lock:
            actions = self.camera_capabilities.setdefault("actions", {})
            item = actions.setdefault(
                action,
                {
                    "label": CAMERA_ACTIONS.get(action, {}).get("label", action),
                    "supported": False,
                    "available": False,
                    "source": "action_result",
                    "last_ok": None,
                    "last_error": "",
                },
            )
            item["last_ok"] = bool(ok)
            item["last_error"] = error
            if ok:
                item["supported"] = True
                item["available"] = True
                item["source"] = "last_action_ok"
            else:
                item["supported"] = False
                item["available"] = False
                item["source"] = "last_action_failed"

    def ensure_nvr_login(self) -> bool:
        with self.lock:
            if self.nvr_login_id:
                return True
            if self.nvr_config is None:
                self.nvr_last_error = "NVR is not configured"
                self.add_nvr_event("nvr_error", {"error": self.nvr_last_error})
                return False

            in_param = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
            in_param.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
            in_param.szIP = self.nvr_config.host.encode()
            in_param.nPort = self.nvr_config.port
            in_param.szUserName = self.nvr_config.username.encode()
            in_param.szPassword = self.nvr_config.password.encode()
            in_param.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
            in_param.pCapParam = None

            out_param = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
            out_param.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

            login_id, device_info, error_msg = self.sdk.LoginWithHighLevelSecurity(in_param, out_param)
            if not login_id:
                self.nvr_last_error = str(error_msg)
                self.add_nvr_event("nvr_login_failed", {"error": self.nvr_last_error})
                return False

            self.nvr_login_id = login_id
            self.nvr_online = True
            self.nvr_last_error = ""
            self.nvr_info = self._device_info_to_dict(device_info)
            self.add_nvr_event("nvr_login_ok", {"device": self.nvr_info})
            return True

    def start_nvr_listen(self) -> bool:
        with self.lock:
            if self.nvr_listening:
                return True
            if not self.ensure_nvr_login():
                return False
            result = self.sdk.StartListenEx(self.nvr_login_id)
            if not result:
                self.nvr_last_error = self.sdk.GetLastErrorMessage()
                self.add_nvr_event("nvr_listen_failed", {"error": self.nvr_last_error})
                return False
            self.nvr_listening = True
            self.add_nvr_event("nvr_listening", {})
            return True

    def connect_nvr_and_listen(self) -> bool:
        return self.start_nvr_listen()

    def disconnect_nvr(self) -> bool:
        with self.lock:
            if self.nvr_listening and self.nvr_login_id:
                self.sdk.StopListen(self.nvr_login_id)
            self.nvr_listening = False
            if self.nvr_login_id:
                self.sdk.Logout(self.nvr_login_id)
            self.nvr_login_id = C_LLONG()
            self.nvr_online = False
            self.add_nvr_event("nvr_logged_out", {})
            return True

    def refresh_nvr_config(self) -> dict[str, Any]:
        if self.nvr_config is None:
            return {"ok": False, "error": "NVR is not configured"}
        if not self.ensure_nvr_login():
            return {"ok": False, "error": self.nvr_last_error}
        with self.lock:
            login_id = int(self.nvr_login_id.value) if hasattr(self.nvr_login_id, "value") else int(self.nvr_login_id)
            channel = self.nvr_config.channel
        configs = self._probe_nvr_configs(login_id, channel)
        with self.lock:
            self.nvr_configs = configs
        self.add_nvr_event(
            "nvr_config",
            {
                "channel": channel,
                "record": bool(configs.get("record", {}).get("ok")),
                "record_mode": bool(configs.get("record_mode", {}).get("ok")),
                "motion_detect": bool(configs.get("motion_detect", {}).get("ok")),
                "snap": bool(configs.get("snap", {}).get("ok")),
            },
        )
        return {"ok": True, "configs": configs}

    def _probe_nvr_configs(self, login_id: int, channel: int) -> dict[str, Any]:
        commands = {
            "record": CFG_CMD_TYPE.RECORD,
            "record_mode": CFG_CMD_TYPE.RECORDMODE,
            "motion_detect": CFG_CMD_TYPE.MOTIONDETECT,
            "snap": CFG_CMD_TYPE.SNAP,
        }
        configs: dict[str, Any] = {}
        for name, command in commands.items():
            buffer = create_string_buffer(512 * 1024)
            ok = bool(self.sdk.GetNewDevConfig(login_id, command, channel, buffer, len(buffer), c_int(0), 3000))
            raw = bytes(buffer).split(b"\x00", 1)[0]
            text = raw.decode("utf-8", errors="replace").strip()
            parsed: Any = None
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            configs[name] = {
                "ok": ok,
                "length": len(raw),
                "sample": text[:180],
                "error": "" if ok else self.sdk.GetLastErrorMessage(),
                "parsed": parsed,
            }
        return configs

    def run_nvr_action(self, action: str) -> dict[str, Any]:
        if self.nvr_config is None:
            return {"ok": False, "error": "NVR is not configured"}
        if action not in NVR_ACTIONS:
            return {"ok": False, "error": f"Unsupported NVR action: {action}"}
        if not self.ensure_nvr_login():
            return {"ok": False, "error": self.nvr_last_error}

        meta = NVR_ACTIONS[action]
        value = self.nvr_config.channel if meta["param"] == "channel" else self.nvr_config.alarm_input
        with self.lock:
            login_id = int(self.nvr_login_id.value) if hasattr(self.nvr_login_id, "value") else int(self.nvr_login_id)
        result = self.sdk.ControlDevice(login_id, meta["control"], c_int(int(value)), 5000)
        ok = bool(result)
        if ok:
            self.nvr_last_error = ""
            self.add_nvr_event(
                "nvr_action_ok",
                {
                    "action": action,
                    "label": meta["label"],
                    "channel": self.nvr_config.channel,
                    "channel_label": self.nvr_config.channel_label,
                    "alarm_input": self.nvr_config.alarm_input,
                },
            )
        else:
            self.nvr_last_error = self.sdk.GetLastErrorMessage()
            self.add_nvr_event(
                "nvr_action_failed",
                {
                    "action": action,
                    "label": meta["label"],
                    "channel": self.nvr_config.channel,
                    "channel_label": self.nvr_config.channel_label,
                    "alarm_input": self.nvr_config.alarm_input,
                    "error": self.nvr_last_error,
                },
            )
        return {"ok": ok, "error": "" if ok else self.nvr_last_error}

    def open_door(self, door: int, direction: str) -> bool:
        with self.lock:
            if not self.ensure_login():
                return False

            param = NET_CTRL_ACCESS_OPEN()
            param.dwSize = sizeof(NET_CTRL_ACCESS_OPEN)
            param.nChannelID = int(door)
            param.emOpenDoorType = EM_OPEN_DOOR_TYPE.EM_OPEN_DOOR_TYPE_REMOTE
            param.emOpenDoorDirection = {
                "enter": EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_FROM_ENTER,
                "leave": EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_FROM_LEAVE,
            }.get(direction, EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_UNKNOWN)
            param.szOperatorID = b"dbs-web-lab"

            result = self.sdk.ControlDevice(self.login_id, CtrlType.ACCESS_OPEN, param, 5000)
            ok = bool(result)
            if ok:
                self.add_event(
                    "remote_open",
                    {
                        "door": door,
                        "door_label": self.door_label(door),
                        "entity_name": self.door_entity_name(door),
                        "direction": direction,
                        "message": "Remote open command sent",
                    },
                )
                self.last_error = ""
            else:
                self.last_error = self.sdk.GetLastErrorMessage()
                self.add_event(
                    "remote_open_failed",
                    {
                        "door": door,
                        "door_label": self.door_label(door),
                        "entity_name": self.door_entity_name(door),
                        "direction": direction,
                        "error": self.last_error,
                    },
                )
            return ok

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "config": {
                    "name": self.config.name,
                    "host": self.config.host,
                    "port": self.config.port,
                    "username": self.config.username,
                    "default_door": self.config.default_door,
                    "doors": list(self.config.doors),
                    "door_names": self.config.door_names,
                    "default_reader": self.config.default_reader,
                },
                "online": self.is_online,
                "listening": self.is_listening,
                "initialized": self.is_initialized,
                "device_info": self.device_info,
                "last_error": self.last_error,
                "doors": self.door_states(),
                "users": self.users_by_id,
                "camera": self.camera_state(),
                "nvr": self.nvr_state(),
                "events": self.events[-200:],
            }

    def camera_state(self) -> dict[str, Any]:
        active = list(self.camera_active_pulses.values())
        cfg = self.camera_config
        return {
            "configured": cfg is not None,
            "config": {
                "name": cfg.name if cfg else "",
                "host": cfg.host if cfg else "",
                "port": cfg.port if cfg else "",
                "username": cfg.username if cfg else "",
                "channel": cfg.channel if cfg else 0,
            },
            "online": self.camera_online,
            "device_info": self.camera_info,
            "last_error": self.camera_last_error,
            "active_pulses": active,
            "capabilities": self.camera_capabilities,
            "actions": self.camera_capabilities.get("actions")
            or {key: {"label": value["label"], "available": False} for key, value in CAMERA_ACTIONS.items()},
            "events": self.camera_events[-80:],
        }

    def nvr_state(self) -> dict[str, Any]:
        cfg = self.nvr_config
        return {
            "configured": cfg is not None,
            "config": {
                "name": cfg.name if cfg else "",
                "host": cfg.host if cfg else "",
                "port": cfg.port if cfg else "",
                "username": cfg.username if cfg else "",
                "channel": cfg.channel if cfg else 0,
                "channel_ui": cfg.channel_ui if cfg else 0,
                "channel_id": cfg.channel_id if cfg else "",
                "channel_name": cfg.channel_name if cfg else "",
                "channel_label": cfg.channel_label if cfg else "",
                "alarm_input": cfg.alarm_input if cfg else 0,
            },
            "online": self.nvr_online,
            "listening": self.nvr_listening,
            "device_info": self.nvr_info,
            "last_error": self.nvr_last_error,
            "configs": self.nvr_configs,
            "actions": {
                key: {
                    "label": value["label"],
                    "class": value.get("class", "btn-outline-primary"),
                    "param": value.get("param", ""),
                }
                for key, value in NVR_ACTIONS.items()
            },
            "events": self.nvr_events[-120:],
        }

    def door_label(self, door: int, event_name: str = "") -> str:
        name = event_name.strip() or self.config.door_names.get(int(door), "").strip()
        return name or f"Przejście {door}"

    def door_entity_name(self, door: int, event_name: str = "") -> str:
        return f"{self.config.name} · {self.door_label(door, event_name)}"

    def door_states(self) -> list[dict[str, Any]]:
        states: dict[int, dict[str, Any]] = {
            door: {
                "door": door,
                "door_label": self.door_label(door),
                "entity_name": self.door_entity_name(door),
                "door_status": "unknown",
                "last_result": "",
                "last_method": "",
                "last_user": "",
                "last_user_id": "",
                "last_card": "",
                "last_reader": "",
                "door_name": "",
                "device_time": "",
                "received_at": "",
            }
            for door in self.config.doors
        }
        for ev in self.events:
            door_value = ev.get("door")
            if door_value is None:
                continue
            try:
                door = int(door_value)
            except (TypeError, ValueError):
                continue
            state = states.setdefault(
                door,
                {
                    "door": door,
                    "door_label": self.door_label(door),
                    "entity_name": self.door_entity_name(door),
                    "door_status": "unknown",
                    "last_result": "",
                    "last_method": "",
                    "last_user": "",
                    "last_user_id": "",
                    "last_card": "",
                    "last_reader": "",
                    "door_name": "",
                    "device_time": "",
                    "received_at": "",
                },
            )
            state["received_at"] = ev.get("received_at", state["received_at"])
            state["device_time"] = ev.get("device_time", state["device_time"])
            if ev.get("door_status"):
                state["door_status"] = ev["door_status"]
            if ev.get("door_name"):
                state["door_name"] = ev["door_name"]
                state["door_label"] = self.door_label(door, ev["door_name"])
                state["entity_name"] = self.door_entity_name(door, ev["door_name"])
            if ev.get("result") or ev.get("kind") in ("remote_open", "remote_open_failed"):
                state["last_result"] = ev.get("result") or ev.get("kind", "")
            if ev.get("open_method"):
                state["last_method"] = ev["open_method"]
            if ev.get("reader"):
                state["last_reader"] = ev["reader"]
            if ev.get("card"):
                state["last_card"] = ev["card"]
            if ev.get("user_id"):
                state["last_user_id"] = ev["user_id"]
                cached_user = self.users_by_id.get(str(ev["user_id"]), {}).get("name", "")
                state["last_user"] = cached_user or ev.get("person_label") or f"ID {ev['user_id']}"
            elif ev.get("person_label"):
                state["last_user"] = ev["person_label"]
        return [states[door] for door in sorted(states)]

    def refresh_seen_users(self) -> dict[str, Any]:
        with self.lock:
            user_ids = sorted({str(ev.get("user_id", "")).strip() for ev in self.events if ev.get("user_id")})
        return self.refresh_users(user_ids)

    def schedule_user_refresh(self, user_id: str) -> None:
        user_id = str(user_id).strip()
        if not user_id:
            return
        with self.lock:
            if user_id in self.users_by_id or user_id in self.user_lookup_inflight:
                return
            self.user_lookup_inflight.add(user_id)
        threading.Thread(target=self._refresh_user_worker, args=(user_id,), daemon=True).start()

    def _refresh_user_worker(self, user_id: str) -> None:
        try:
            self.refresh_users([user_id])
        finally:
            with self.lock:
                self.user_lookup_inflight.discard(user_id)

    def refresh_users(self, user_ids: list[str]) -> dict[str, Any]:
        clean_ids = [user_id for user_id in dict.fromkeys(user_ids) if user_id][:100]
        if not clean_ids:
            return {"ok": True, "count": 0, "users": self.users_by_id}
        with self.lock:
            if not self.ensure_login():
                return {"ok": False, "error": self.last_error}

            in_param = NET_IN_ACCESS_USER_SERVICE_GET()
            in_param.dwSize = sizeof(NET_IN_ACCESS_USER_SERVICE_GET)
            in_param.nUserNum = len(clean_ids)
            packed_user_ids = bytearray(3200)
            for index, user_id in enumerate(clean_ids):
                encoded = user_id.encode("utf-8")[:31]
                offset = index * 32
                packed_user_ids[offset : offset + len(encoded)] = encoded
            in_param.szUserID = bytes(packed_user_ids)

            users = (NET_ACCESS_USER_INFO * len(clean_ids))()
            fail_codes = (C_ENUM * len(clean_ids))()
            out_param = NET_OUT_ACCESS_USER_SERVICE_GET()
            out_param.dwSize = sizeof(NET_OUT_ACCESS_USER_SERVICE_GET)
            out_param.nMaxRetNum = len(clean_ids)
            out_param.pUserInfo = users
            out_param.pFailCode = fail_codes

            result = self.sdk.OperateAccessUserService(
                self.login_id,
                EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_GET,
                in_param,
                out_param,
                5000,
            )
            if not result:
                self.last_error = self.sdk.GetLastErrorMessage()
                self.add_event("user_refresh_failed", {"error": self.last_error})
                return {"ok": False, "error": self.last_error}

            for index, requested_id in enumerate(clean_ids):
                info = users[index]
                user_id = decode_bytes(info.szUserID) or requested_id
                name = decode_bytes(info.szNameEx) if bool(info.bUseNameEx) else ""
                name = name or decode_bytes(info.szName)
                self.users_by_id[user_id] = {
                    "user_id": user_id,
                    "name": name,
                    "status": int(info.nUserStatus),
                    "door_count": int(info.nDoorNum),
                    "fail_code": int(fail_codes[index]),
                }
            self.add_event("user_refresh", {"message": f"Users refreshed: {len(clean_ids)}"})
            return {"ok": True, "count": len(clean_ids), "users": self.users_by_id}

    def add_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self.lock:
            item = {
                "id": self.next_event_id,
                "kind": kind,
                "received_at": dt.datetime.now().isoformat(timespec="seconds"),
                **payload,
            }
            self.next_event_id += 1
            self.events.append(item)
            self.events = self.events[-300:]

    def add_camera_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self.lock:
            item = {
                "id": self.next_camera_event_id,
                "kind": kind,
                "received_at": dt.datetime.now().isoformat(timespec="seconds"),
                **payload,
            }
            self.next_camera_event_id += 1
            self.camera_events.append(item)
            self.camera_events = self.camera_events[-160:]

    def add_nvr_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self.lock:
            item = {
                "id": self.next_nvr_event_id,
                "kind": kind,
                "received_at": dt.datetime.now().isoformat(timespec="seconds"),
                **payload,
            }
            self.next_nvr_event_id += 1
            self.nvr_events.append(item)
            self.nvr_events = self.nvr_events[-220:]

    def handle_message(self, l_command, l_login_id, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, n_event_id) -> None:
        if self.nvr_login_id and int(l_login_id) == int(self.nvr_login_id):
            self.handle_nvr_message(l_command, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, n_event_id)
            return

        if self.login_id and int(l_login_id) != int(self.login_id):
            return

        command = int(l_command)
        if command == int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_EVENT):
            info = cast(p_buf, POINTER(NET_A_ALARM_ACCESS_CTL_EVENT_INFO)).contents
            user_id = decode_bytes(info.szUserID)
            card_name = decode_bytes(info.szCardNameEx) or decode_bytes(info.szCardName)
            user_name = self.users_by_id.get(user_id, {}).get("name", "") if user_id else ""
            person_label = card_name or user_name or (f"ID {user_id}" if user_id else "")
            door_name = decode_bytes(info.szDoorName) if hasattr(info, "szDoorName") else ""
            door = int(info.nDoor)
            self.add_event(
                "access_event",
                {
                    "result": normalize_access_result(info.bStatus, info.nErrorCode),
                    "door": door,
                    "door_name": door_name,
                    "door_label": self.door_label(door, door_name),
                    "entity_name": self.door_entity_name(door, door_name),
                    "event_type": normalize_event_type(info.emEventType),
                    "event_type_raw": int(info.emEventType),
                    "status": int(info.bStatus),
                    "open_method": normalize_open_method(info.emOpenMethod),
                    "open_method_raw": int(info.emOpenMethod),
                    "reader": decode_bytes(info.szReaderID),
                    "user_id": user_id,
                    "user_name": user_name,
                    "card": decode_bytes(info.szCardNo),
                    "card_name": card_name,
                    "person_label": person_label,
                    "error": int(info.nErrorCode),
                    "device_time": sdk_time_to_string(info.stuTime),
                },
            )
            self.schedule_user_refresh(user_id)
            return

        if command == int(SDK_ALARM_TYPE.ALARM_ACCESS_CTL_STATUS):
            info = cast(p_buf, POINTER(NET_A_ALARM_ACCESS_CTL_STATUS_INFO)).contents
            door = int(info.nDoor)
            self.add_event(
                "access_status",
                {
                    "door": door,
                    "door_label": self.door_label(door),
                    "entity_name": self.door_entity_name(door),
                    "door_status": normalize_door_status(info.emStatus),
                    "door_status_raw": int(info.emStatus),
                    "serial": decode_bytes(info.szSerialNumber),
                    "device_time": sdk_time_to_string(info.stuTime),
                },
            )
            return

        self.add_event(
            "raw_alarm",
            {
                "command": command,
                "command_name": sdk_alarm_name(command),
                "event_id": int(n_event_id),
                "length": int(dw_buf_len),
                "ip": decode_bytes(pch_dvr_ip),
                "port": int(n_dvr_port),
            },
        )

    def handle_nvr_message(self, l_command, p_buf, dw_buf_len, pch_dvr_ip, n_dvr_port, n_event_id) -> None:
        command = int(l_command)
        payload: dict[str, Any] = {
            "command": command,
            "command_name": sdk_alarm_name(command),
            "event_id": int(n_event_id),
            "length": int(dw_buf_len),
            "ip": decode_bytes(pch_dvr_ip),
            "port": int(n_dvr_port),
        }
        if command == int(SDK_ALARM_TYPE.MOTION_ALARM_EX):
            raw = string_at(p_buf, int(dw_buf_len))
            payload["active_motion_channels"] = [index for index, value in enumerate(raw) if value]
        elif command == int(SDK_ALARM_TYPE.ALARM_ALARM_EX):
            raw = string_at(p_buf, int(dw_buf_len))
            payload["active_alarm_inputs"] = [index for index, value in enumerate(raw) if value]
        elif command == int(SDK_ALARM_TYPE.EVENT_MOTIONDETECT):
            info = cast(p_buf, POINTER(ALARM_MOTIONDETECT_INFO)).contents
            payload["channel"] = int(info.nChannelID)
            payload["action"] = int(info.nEventAction)
            payload["motion_event_id"] = int(info.nEventID)
        self.add_nvr_event("nvr_callback", payload)

    def on_disconnect(self, l_login_id, pch_dvr_ip, n_dvr_port, dw_user) -> None:
        if self.camera_login_id and int(l_login_id) == int(self.camera_login_id):
            self.camera_online = False
            self.add_camera_event("camera_disconnect", {"ip": decode_bytes(pch_dvr_ip), "port": int(n_dvr_port)})
            return
        if self.nvr_login_id and int(l_login_id) == int(self.nvr_login_id):
            self.nvr_online = False
            self.add_nvr_event("nvr_disconnect", {"ip": decode_bytes(pch_dvr_ip), "port": int(n_dvr_port)})
            return
        self.is_online = False
        self.add_event("disconnect", {"ip": decode_bytes(pch_dvr_ip), "port": int(n_dvr_port)})

    def on_reconnect(self, l_login_id, pch_dvr_ip, n_dvr_port, dw_user) -> None:
        if self.camera_login_id and int(l_login_id) == int(self.camera_login_id):
            self.camera_online = True
            self.add_camera_event("camera_reconnect", {"ip": decode_bytes(pch_dvr_ip), "port": int(n_dvr_port)})
            return
        if self.nvr_login_id and int(l_login_id) == int(self.nvr_login_id):
            self.nvr_online = True
            self.add_nvr_event("nvr_reconnect", {"ip": decode_bytes(pch_dvr_ip), "port": int(n_dvr_port)})
            return
        self.is_online = True
        self.add_event("reconnect", {"ip": decode_bytes(pch_dvr_ip), "port": int(n_dvr_port)})


HTML = r"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DBS Dahua Access Lab</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root { --line: #d9dee7; --ink: #17202a; --soft: #f5f7fb; --accent: #1f6feb; }
    body { background: #eef2f7; color: var(--ink); letter-spacing: 0; }
    .app-shell { max-width: 1440px; }
    .topbar { background: #ffffff; border-bottom: 1px solid var(--line); }
    .panel { background: #ffffff; border: 1px solid var(--line); border-radius: 8px; }
    .metric { min-height: 92px; }
    .event-table { font-size: .875rem; }
    .event-table td, .event-table th { white-space: nowrap; vertical-align: middle; }
    .codeish { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    .door-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: .75rem; }
    .door-tile { border: 1px solid var(--line); border-radius: 8px; padding: .85rem; background: #fff; min-height: 196px; }
    .door-title { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
    .door-meta { min-height: 4.5rem; }
    .door-actions { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; }
    .camera-actions, .nvr-actions { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: .5rem; }
    .camera-log, .nvr-log { max-height: 156px; overflow: auto; font-size: .82rem; }
    .status-dot { display: inline-block; width: .65rem; height: .65rem; border-radius: 50%; margin-right: .35rem; background: #adb5bd; }
    .status-dot.on { background: #188038; }
    .status-dot.warn { background: #b76e00; }
    .status-dot.off { background: #c5221f; }
    .toolbar .btn { min-width: 7.5rem; }
    .sticky-head { position: sticky; top: 0; background: #fff; z-index: 1; }
    .scrollbox { max-height: calc(100vh - 340px); overflow: auto; }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="app-shell mx-auto px-3 py-3 d-flex align-items-center justify-content-between gap-3">
      <div>
        <h1 class="h4 mb-0">DBS Dahua Access Lab</h1>
        <div class="text-secondary small" id="controllerLine">Ładowanie...</div>
      </div>
      <div class="d-flex align-items-center gap-3">
        <span class="small"><span id="onlineDot" class="status-dot"></span><span id="onlineText">offline</span></span>
        <span class="small"><span id="listenDot" class="status-dot"></span><span id="listenText">listener off</span></span>
      </div>
    </div>
  </header>

  <main class="app-shell mx-auto p-3">
    <section class="row g-3 mb-3">
      <div class="col-12 col-lg-3">
        <div class="panel metric p-3">
          <div class="text-secondary small">Serial</div>
          <div class="h5 mb-0 codeish" id="serial">-</div>
        </div>
      </div>
      <div class="col-6 col-lg-2">
        <div class="panel metric p-3">
          <div class="text-secondary small">Alarm In</div>
          <div class="h5 mb-0" id="alarmIn">-</div>
        </div>
      </div>
      <div class="col-6 col-lg-2">
        <div class="panel metric p-3">
          <div class="text-secondary small">Alarm Out</div>
          <div class="h5 mb-0" id="alarmOut">-</div>
        </div>
      </div>
      <div class="col-12 col-lg-5">
        <div class="panel p-3">
          <div class="d-flex flex-wrap gap-2 toolbar">
            <button class="btn btn-primary" onclick="apiPost('/api/connect')">Połącz + nasłuch</button>
            <button class="btn btn-outline-secondary" onclick="apiPost('/api/listen/stop')">Pauza nasłuchu</button>
            <button class="btn btn-outline-danger" onclick="apiPost('/api/disconnect')">Rozłącz</button>
          </div>
          <div class="small text-danger mt-2" id="lastError"></div>
        </div>
      </div>
    </section>

    <section class="row g-3 mb-3">
      <div class="col-12">
        <div class="panel p-3">
          <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
            <div>
              <h2 class="h6 mb-0">Kamera testowa</h2>
              <div class="small text-secondary" id="cameraLine">Brak konfiguracji kamery.</div>
            </div>
            <div class="d-flex flex-wrap align-items-center gap-2">
              <span class="small"><span id="cameraDot" class="status-dot"></span><span id="cameraText">offline</span></span>
              <button class="btn btn-sm btn-primary" onclick="apiPost('/api/camera/connect')">Połącz kamerę</button>
              <button class="btn btn-sm btn-outline-secondary" onclick="refreshCameraCaps(false)">Sprawdź funkcje</button>
              <button class="btn btn-sm btn-outline-warning" onclick="refreshCameraCaps(true)">Test akcji</button>
              <button class="btn btn-sm btn-outline-danger" onclick="apiPost('/api/camera/disconnect')">Rozłącz kamerę</button>
            </div>
          </div>
          <div class="row g-3">
            <div class="col-12 col-lg-3">
              <label class="form-label small">Kanał kamery</label>
              <input class="form-control form-control-sm" id="cameraChannelInput" type="number" min="0" step="1" value="0">
            </div>
            <div class="col-12 col-lg-9">
              <div class="small text-danger mt-lg-4 pt-lg-1" id="cameraLastError"></div>
            </div>
          </div>
          <div class="d-flex flex-wrap align-items-center gap-3 mt-3">
            <div class="small text-secondary" id="cameraCapsLine">Funkcje: -</div>
            <div class="form-check form-switch small">
              <input class="form-check-input" type="checkbox" id="showUnsupportedCameraActions" onchange="renderCamera(state.camera)">
              <label class="form-check-label" for="showUnsupportedCameraActions">Pokaż ukryte</label>
            </div>
          </div>
          <div class="camera-actions mt-3" id="cameraActions"></div>
          <div class="mt-3">
            <div class="small text-secondary mb-1">Log kamery</div>
            <div class="camera-log border rounded p-2 bg-light" id="cameraLog">-</div>
          </div>
        </div>
      </div>
    </section>

    <section class="row g-3 mb-3">
      <div class="col-12">
        <div class="panel p-3">
          <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
            <div>
              <h2 class="h6 mb-0">Rejestrator NVR</h2>
              <div class="small text-secondary" id="nvrLine">Brak konfiguracji NVR.</div>
            </div>
            <div class="d-flex flex-wrap align-items-center gap-2">
              <span class="small"><span id="nvrDot" class="status-dot"></span><span id="nvrText">offline</span></span>
              <span class="small"><span id="nvrListenDot" class="status-dot"></span><span id="nvrListenText">listener off</span></span>
              <button class="btn btn-sm btn-primary" onclick="apiPost('/api/nvr/connect')">Połącz + nasłuch</button>
              <button class="btn btn-sm btn-outline-secondary" onclick="apiPost('/api/nvr/config/refresh')">Odśwież konfigurację</button>
              <button class="btn btn-sm btn-outline-danger" onclick="apiPost('/api/nvr/disconnect')">Rozłącz NVR</button>
            </div>
          </div>
          <div class="row g-3">
            <div class="col-6 col-lg-2">
              <div class="text-secondary small">Kanał SDK</div>
              <div class="h5 mb-0 codeish" id="nvrChannel">-</div>
            </div>
            <div class="col-6 col-lg-2">
              <div class="text-secondary small">Kanał UI</div>
              <div class="h5 mb-0 codeish" id="nvrChannelUi">-</div>
            </div>
            <div class="col-12 col-lg-4">
              <div class="text-secondary small">Cel testu</div>
              <div class="h5 mb-0" id="nvrChannelLabel">-</div>
            </div>
            <div class="col-12 col-lg-4">
              <div class="text-secondary small">Status konfiguracji</div>
              <div class="small" id="nvrConfigLine">-</div>
            </div>
          </div>
          <div class="small text-danger mt-2" id="nvrLastError"></div>
          <div class="nvr-actions mt-3" id="nvrActions"></div>
          <div class="mt-3">
            <div class="small text-secondary mb-1">Log NVR</div>
            <div class="nvr-log border rounded p-2 bg-light" id="nvrLog">-</div>
          </div>
        </div>
      </div>
    </section>

    <section class="row g-3">
      <div class="col-12 col-xl-5">
        <div class="panel p-3">
          <div class="d-flex align-items-center justify-content-between gap-2 mb-3">
            <h2 class="h6 mb-0">Drzwi</h2>
            <button class="btn btn-sm btn-outline-secondary" onclick="apiPost('/api/users/refresh')">Odśwież użytkowników</button>
          </div>
          <div class="door-grid" id="doorsGrid"></div>
        </div>
      </div>

      <div class="col-12 col-xl-7">
        <div class="panel">
          <div class="p-3 border-bottom d-flex align-items-center justify-content-between">
            <h2 class="h6 mb-0">Zdarzenia</h2>
            <button class="btn btn-sm btn-outline-secondary" onclick="refresh()">Odśwież</button>
          </div>
          <div class="scrollbox">
            <table class="table table-sm table-hover event-table mb-0">
              <thead class="sticky-head">
                <tr>
                  <th>#</th><th>Czas</th><th>Typ</th><th>Wynik / status</th><th>Drzwi</th><th>Nazwa</th><th>Czytnik</th><th>Metoda</th><th>Karta</th><th>Użytkownik</th><th>User ID</th><th>Błąd</th><th>Czas urządzenia</th>
                </tr>
              </thead>
              <tbody id="eventsBody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    let state = null;

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      })[ch]);
    }

    function badgeClass(value) {
      if (value === 'access_granted' || value === 'open' || value === 'remote_open') return 'text-bg-success';
      if (value === 'access_denied' || value === 'remote_open_failed') return 'text-bg-danger';
      if (value === 'closed') return 'text-bg-secondary';
      return 'text-bg-light text-dark';
    }

    async function apiPost(path, body = {}) {
      const res = await fetch(path, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
      const data = await res.json();
      if (!data.ok && data.error) alert(data.error);
      await refresh();
    }

    async function openDoor(door, direction) {
      await apiPost('/api/door/open', { door, direction });
    }

    async function cameraAction(action) {
      const channel = Number(document.getElementById('cameraChannelInput').value || 0);
      await apiPost('/api/camera/action', { action, channel });
    }

    async function nvrAction(action) {
      await apiPost('/api/nvr/action', { action });
    }

    async function refreshCameraCaps(testActions) {
      const channel = Number(document.getElementById('cameraChannelInput').value || 0);
      await apiPost('/api/camera/capabilities/refresh', { channel, test_actions: Boolean(testActions) });
    }

    function renderDoors(doors) {
      const html = (doors || []).map(item => {
        const status = item.door_status || 'unknown';
        const result = item.last_result || '';
        const user = item.last_user || item.last_user_id || '';
        const doorTitle = item.entity_name || item.door_label || `Przejście ${item.door}`;
        const doorSubtitle = item.door_name || item.door_label || '';
        return `<div class="door-tile">
          <div class="door-title mb-2">
            <div>
              <div class="fw-semibold">${escapeHtml(doorTitle)}</div>
              <div class="small text-secondary">${escapeHtml(doorSubtitle)} <span class="codeish">door ${escapeHtml(item.door)}</span></div>
            </div>
            <span class="badge ${badgeClass(status)}">${escapeHtml(status)}</span>
          </div>
          <div class="door-meta small">
            <div>Ostatnio: <span class="badge ${badgeClass(result)}">${escapeHtml(result || '-')}</span></div>
            <div>Metoda: <span class="codeish">${escapeHtml(item.last_method || '-')}</span></div>
            <div>Użytkownik: <span>${escapeHtml(user || '-')}</span></div>
            <div>Karta: <span class="codeish">${escapeHtml(item.last_card || '-')}</span></div>
            <div>Czytnik: <span class="codeish">${escapeHtml(item.last_reader || '-')}</span></div>
          </div>
          <div class="door-actions mt-3">
            <button class="btn btn-warning btn-sm" onclick="openDoor(${Number(item.door)}, 'enter')">Otwórz wejście</button>
            <button class="btn btn-outline-warning btn-sm" onclick="openDoor(${Number(item.door)}, 'leave')">Otwórz wyjście</button>
          </div>
        </div>`;
      }).join('');
      document.getElementById('doorsGrid').innerHTML = html || '<div class="text-secondary small">Brak skonfigurowanych drzwi.</div>';
    }

    function renderCamera(camera) {
      const configured = camera && camera.configured;
      const cfg = configured ? camera.config : {};
      document.getElementById('cameraLine').textContent = configured
        ? `${cfg.name} · ${cfg.host}:${cfg.port} · user ${cfg.username}`
        : 'Brak konfiguracji kamery.';
      document.getElementById('cameraDot').className = `status-dot ${camera && camera.online ? 'on' : 'off'}`;
      document.getElementById('cameraText').textContent = camera && camera.online ? 'online' : 'offline';
      document.getElementById('cameraLastError').textContent = camera ? (camera.last_error || '') : '';
      if (configured && !document.getElementById('cameraChannelInput').dataset.touched) {
        document.getElementById('cameraChannelInput').value = cfg.channel ?? 0;
        document.getElementById('cameraChannelInput').dataset.touched = '1';
      }

      const caps = camera && camera.capabilities ? camera.capabilities : {};
      const features = caps.features || {};
      const capsDetails = [
        `kanał ${features.channel_exists ? 'OK' : 'brak'}`,
        `snap ${features.snapshot_config ? 'OK' : 'brak'}`,
        `record ${features.record_config ? 'OK' : 'brak'}`,
        `motion ${features.motion_detect_config ? 'OK' : 'brak'}`,
        `alarm in ${features.alarm_in_ports ?? 0}`,
        `alarm out ${features.alarm_out_ports ?? 0}`
      ];
      document.getElementById('cameraCapsLine').textContent = caps.checked_at
        ? `Funkcje ${caps.checked_at}: ${capsDetails.join(' · ')}`
        : 'Funkcje: nie sprawdzono';

      const actions = camera && camera.actions ? camera.actions : {};
      const showUnsupported = document.getElementById('showUnsupportedCameraActions').checked;
      const buttons = Object.entries(actions).filter(([, value]) => {
        return showUnsupported || value.available || value.supported;
      }).map(([key, value]) => {
        const available = Boolean(value.available || value.supported);
        const cls = available ? 'btn-outline-primary' : 'btn-outline-secondary';
        const canClick = configured && (available || showUnsupported);
        const suffix = available ? '' : ' · test';
        const title = [value.source, value.last_error].filter(Boolean).join(' · ');
        return `<button class="btn btn-sm ${cls}" title="${escapeHtml(title)}" ${canClick ? '' : 'disabled'} onclick="cameraAction('${escapeHtml(key)}')">${escapeHtml(value.label)}${escapeHtml(suffix)}</button>`;
      }).join('');
      document.getElementById('cameraActions').innerHTML = buttons || '<div class="small text-secondary">Brak potwierdzonych akcji kamery.</div>';

      const log = (camera && camera.events || []).slice().reverse().map(ev => {
        const detail = ev.error || ev.file || ev.bytes || ev.action || '';
        return `<div><span class="text-secondary">${escapeHtml(ev.received_at)}</span> <span>${escapeHtml(ev.kind)}</span> <span class="codeish">${escapeHtml(detail)}</span></div>`;
      }).join('');
      document.getElementById('cameraLog').innerHTML = log || '-';
    }

    function renderNvr(nvr) {
      const configured = nvr && nvr.configured;
      const cfg = configured ? nvr.config : {};
      document.getElementById('nvrLine').textContent = configured
        ? `${cfg.name} · ${cfg.host}:${cfg.port} · user ${cfg.username}`
        : 'Brak konfiguracji NVR.';
      document.getElementById('nvrDot').className = `status-dot ${nvr && nvr.online ? 'on' : 'off'}`;
      document.getElementById('nvrText').textContent = nvr && nvr.online ? 'online' : 'offline';
      document.getElementById('nvrListenDot').className = `status-dot ${nvr && nvr.listening ? 'on' : 'warn'}`;
      document.getElementById('nvrListenText').textContent = nvr && nvr.listening ? 'listener on' : 'listener off';
      document.getElementById('nvrChannel').textContent = configured ? cfg.channel : '-';
      document.getElementById('nvrChannelUi').textContent = configured ? cfg.channel_ui : '-';
      document.getElementById('nvrChannelLabel').textContent = configured ? cfg.channel_label : '-';
      document.getElementById('nvrLastError').textContent = nvr ? (nvr.last_error || '') : '';

      const configs = nvr && nvr.configs ? nvr.configs : {};
      const configParts = ['record', 'record_mode', 'motion_detect', 'snap'].map(key => {
        if (!configs[key]) return `${key}: -`;
        return `${key}: ${configs[key].ok ? 'OK' : 'błąd'}`;
      });
      document.getElementById('nvrConfigLine').textContent = configParts.join(' · ');

      const actions = nvr && nvr.actions ? nvr.actions : {};
      const buttons = Object.entries(actions).map(([key, value]) => {
        const disabled = configured ? '' : 'disabled';
        const cls = value.class || 'btn-outline-primary';
        return `<button class="btn btn-sm ${escapeHtml(cls)}" ${disabled} onclick="nvrAction('${escapeHtml(key)}')">${escapeHtml(value.label)}</button>`;
      }).join('');
      document.getElementById('nvrActions').innerHTML = buttons || '<div class="small text-secondary">Brak akcji NVR.</div>';

      const log = (nvr && nvr.events || []).slice().reverse().map(ev => {
        const details = [
          ev.label || ev.action || '',
          ev.command_name || '',
          ev.channel !== undefined ? `ch=${ev.channel}` : '',
          ev.channel_label || '',
          ev.active_motion_channels ? `motion=[${ev.active_motion_channels.join(',')}]` : '',
          ev.active_alarm_inputs ? `alarm=[${ev.active_alarm_inputs.join(',')}]` : '',
          ev.error || ''
        ].filter(Boolean).join(' · ');
        return `<div><span class="text-secondary">${escapeHtml(ev.received_at)}</span> <span>${escapeHtml(ev.kind)}</span> <span class="codeish">${escapeHtml(details)}</span></div>`;
      }).join('');
      document.getElementById('nvrLog').innerHTML = log || '-';
    }

    function render(next) {
      state = next;
      const cfg = state.config;
      document.getElementById('controllerLine').textContent = `${cfg.name} · ${cfg.host}:${cfg.port} · user ${cfg.username}`;
      document.getElementById('serial').textContent = state.device_info.sSerialNumber || '-';
      document.getElementById('alarmIn').textContent = state.device_info.nAlarmInPortNum ?? '-';
      document.getElementById('alarmOut').textContent = state.device_info.nAlarmOutPortNum ?? '-';
      document.getElementById('lastError').textContent = state.last_error || '';

      document.getElementById('onlineDot').className = `status-dot ${state.online ? 'on' : 'off'}`;
      document.getElementById('onlineText').textContent = state.online ? 'online' : 'offline';
      document.getElementById('listenDot').className = `status-dot ${state.listening ? 'on' : 'warn'}`;
      document.getElementById('listenText').textContent = state.listening ? 'listener on' : 'listener off';

      renderDoors(state.doors);
      renderCamera(state.camera);
      renderNvr(state.nvr);

      const rows = [...state.events].reverse().map(ev => {
        const status = ev.result || ev.door_status || ev.kind || '';
        const cachedUser = ev.user_id && state.users && state.users[String(ev.user_id)] ? state.users[String(ev.user_id)].name : '';
        const person = cachedUser || ev.user_name || ev.person_label || ev.card_name || '';
        return `<tr>
          <td>${escapeHtml(ev.id)}</td>
          <td class="codeish">${escapeHtml(ev.received_at || '')}</td>
          <td>${escapeHtml(ev.kind || '')}</td>
          <td><span class="badge ${badgeClass(status)}">${escapeHtml(status)}</span></td>
          <td>${escapeHtml(ev.door ?? '')}</td>
          <td>${escapeHtml(ev.entity_name || ev.door_label || ev.door_name || '')}</td>
          <td>${escapeHtml(ev.reader ?? '')}</td>
          <td>${escapeHtml(ev.open_method ?? '')}</td>
          <td class="codeish">${escapeHtml(ev.card ?? '')}</td>
          <td>${escapeHtml(person)}</td>
          <td>${escapeHtml(ev.user_id ?? '')}</td>
          <td>${escapeHtml(ev.error ?? ev.command_name ?? '')}</td>
          <td class="codeish">${escapeHtml(ev.device_time ?? '')}</td>
        </tr>`;
      }).join('');
      document.getElementById('eventsBody').innerHTML = rows || '<tr><td colspan="13" class="text-secondary p-3">Brak zdarzeń.</td></tr>';
    }

    async function refresh() {
      const res = await fetch('/api/state');
      render(await res.json());
    }

    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


class RequestHandler(BaseHTTPRequestHandler):
    controller: DahuaWebController

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_html(HTML)
            return
        if path == "/api/state":
            self.send_json(self.controller.state())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self.read_json()
        try:
            if path == "/api/connect":
                ok = self.controller.connect_and_listen()
                self.send_json({"ok": ok, "error": "" if ok else self.controller.last_error})
                return
            if path == "/api/listen/start":
                self.send_json({"ok": self.controller.start_listen()})
                return
            if path == "/api/listen/stop":
                self.send_json({"ok": self.controller.stop_listen()})
                return
            if path == "/api/disconnect":
                self.send_json({"ok": self.controller.disconnect()})
                return
            if path == "/api/users/refresh":
                self.send_json(self.controller.refresh_seen_users())
                return
            if path == "/api/door/open":
                door = int(body.get("door", self.controller.config.default_door))
                direction = str(body.get("direction", "unknown"))
                ok = self.controller.open_door(door, direction)
                self.send_json({"ok": ok, "error": "" if ok else self.controller.last_error})
                return
            if path == "/api/camera/connect":
                self.send_json({"ok": self.controller.ensure_camera_login(), "error": self.controller.camera_last_error})
                return
            if path == "/api/camera/disconnect":
                self.send_json({"ok": self.controller.disconnect_camera()})
                return
            if path == "/api/camera/capabilities/refresh":
                channel = int(body.get("channel", self.controller.camera_config.channel if self.controller.camera_config else 0))
                test_actions = bool(body.get("test_actions", False))
                self.send_json(self.controller.refresh_camera_capabilities(channel, test_actions=test_actions))
                return
            if path in ("/api/camera/action", "/api/camera/pulse"):
                action = str(body.get("action", ""))
                channel = int(body.get("channel", self.controller.camera_config.channel if self.controller.camera_config else 0))
                self.send_json(self.controller.run_camera_action(action, channel))
                return
            if path == "/api/nvr/connect":
                ok = self.controller.connect_nvr_and_listen()
                self.send_json({"ok": ok, "error": "" if ok else self.controller.nvr_last_error})
                return
            if path == "/api/nvr/disconnect":
                self.send_json({"ok": self.controller.disconnect_nvr()})
                return
            if path == "/api/nvr/config/refresh":
                self.send_json(self.controller.refresh_nvr_config())
                return
            if path == "/api/nvr/action":
                action = str(body.get("action", ""))
                self.send_json(self.controller.run_nvr_action(action))
                return
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Dahua NetSDK web dashboard.")
    parser.add_argument("--host", default=os.environ.get("DBS_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DBS_WEB_PORT", "8787")))
    parser.add_argument("--env-file", type=Path, default=None, help="Optional env file loader for local lab use.")
    parser.add_argument("--open-browser", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)

    config = load_config()
    camera_config = load_camera_config()
    nvr_config = load_nvr_config()
    controller = DahuaWebController(config, camera_config, nvr_config)
    controller.start()

    RequestHandler.controller = controller
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"DBS Dahua Access Lab running at {url}")
    print("Secrets are read from environment variables; they are not served to the browser.")

    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    def shutdown(signum, frame):
        print("\nStopping web dashboard...")
        controller.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    finally:
        controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
