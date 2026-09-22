"""Dahua NetSDK access-controller client."""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from collections.abc import Callable
from ctypes import POINTER, c_char, c_char_p, c_int, c_long, cast, create_string_buffer, sizeof
from typing import Any, Protocol

from .exceptions import DahuaAuthError, DahuaConnectionError, DahuaSdkUnavailable
from .models import AccessControllerConfig, AccessDeviceInfo, AccessDoor, AccessEvent, AccessUser
from .normalizer import (
    enum_name,
    normalize_access_result,
    normalize_door_status_name,
    normalize_event_type_name,
    normalize_open_method_name,
    resolve_door_label,
)

_LOGGER = logging.getLogger(__name__)


EventCallback = Callable[[AccessEvent], None]


class DahuaAccessClient(Protocol):
    """Protocol implemented by Dahua access clients."""

    device_info: AccessDeviceInfo | None

    def connect(self) -> AccessDeviceInfo:
        """Connect and return device information."""

    def disconnect(self) -> None:
        """Disconnect from the controller."""

    def discover_doors(self) -> list[AccessDoor]:
        """Return doors/passages known by the controller."""

    def start_listening(self, callback: EventCallback) -> None:
        """Start push-event listening."""

    def stop_listening(self) -> None:
        """Stop push-event listening."""

    def open_door(self, door_id: int, direction: str = "unknown") -> None:
        """Send a remote open command."""

    def get_user(self, user_id: str) -> AccessUser | None:
        """Fetch a user by access-control user id."""


def _decode_bytes(value: Any) -> str:
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


def _sdk_time_to_string(value: Any) -> str:
    fields = ("dwYear", "dwMonth", "dwDay", "dwHour", "dwMinute", "dwSecond")
    try:
        parts = [int(getattr(value, field)) for field in fields]
        if parts[0] <= 0:
            return ""
        return dt.datetime(*parts).isoformat()
    except (TypeError, ValueError):
        return ""


class NetSDKAccessClient:
    """Dahua access-controller client backed by Dahua NetSDK."""

    def __init__(self, config: AccessControllerConfig, name_hint: str = "") -> None:
        self.config = config
        self.name_hint = name_hint or config.host
        self.device_info: AccessDeviceInfo | None = None
        self._callback: EventCallback | None = None
        self._lock = threading.RLock()
        self._login_id: Any = None
        self._listening = False
        self._sdk_modules: dict[str, Any] | None = None
        self._sdk: Any = None
        self._message_callback: Any = None

    def connect(self) -> AccessDeviceInfo:
        """Connect to the controller and return device metadata."""
        with self._lock:
            if self.device_info and self._login_id:
                return self.device_info
            sdk = self._ensure_sdk()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            in_param = structs.NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
            in_param.dwSize = sizeof(structs.NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
            in_param.szIP = self.config.host.encode()
            in_param.nPort = self.config.port
            in_param.szUserName = self.config.username.encode()
            in_param.szPassword = self.config.password.encode()
            in_param.emSpecCap = enums.EM_LOGIN_SPAC_CAP_TYPE.TCP
            in_param.pCapParam = None

            out_param = structs.NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
            out_param.dwSize = sizeof(structs.NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

            login_id, raw_info, error_msg = sdk.LoginWithHighLevelSecurity(in_param, out_param)
            if not self._is_handle(login_id):
                message = str(error_msg)
                if "password" in message.lower() or "login" in message.lower():
                    raise DahuaAuthError(message)
                raise DahuaConnectionError(message)

            self._login_id = login_id
            self.device_info = self._build_device_info(raw_info)
            return self.device_info

    def disconnect(self) -> None:
        """Disconnect from the controller."""
        with self._lock:
            if self._sdk and self._listening and self._login_id:
                self._sdk.StopListen(self._login_id)
            self._listening = False
            if self._sdk and self._login_id:
                self._sdk.Logout(self._login_id)
            self._login_id = None

    def discover_doors(self) -> list[AccessDoor]:
        """Return doors/passages discovered from AccessControl config."""
        self.connect()
        doors: list[AccessDoor] = []
        consecutive_failures = 0
        for channel in range(32):
            buffer = create_string_buffer(512 * 1024)
            error = c_int(0)
            ok = bool(self._sdk.GetNewDevConfig(self._login_id, "AccessControl", channel, buffer, len(buffer), error, 3000))
            raw = bytes(buffer).split(b"\x00", 1)[0]
            if not ok and not raw:
                consecutive_failures += 1
                if doors and consecutive_failures >= 4:
                    break
                continue

            consecutive_failures = 0
            door_id = channel + 1
            label = self._label_from_access_control_config(raw) or resolve_door_label(door_id)
            doors.append(AccessDoor(door_id=door_id, label=label, source="config" if raw else "probe"))

        if doors:
            return doors
        return [AccessDoor(door_id=index, label=resolve_door_label(index), source="default") for index in range(1, 5)]

    def start_listening(self, callback: EventCallback) -> None:
        """Start push-event listening."""
        with self._lock:
            self.connect()
            self._callback = callback
            sdk = self._ensure_sdk()
            callbacks = self._sdk_modules["callbacks"]
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            if self._message_callback is None:
                c_llong = structs.C_LLONG
                c_dword = structs.C_DWORD
                c_ldword = structs.C_LDWORD

                @callbacks.CB_FUNCTYPE(
                    None,
                    c_long,
                    c_llong,
                    POINTER(c_char),
                    c_dword,
                    POINTER(c_char),
                    c_long,
                    c_int,
                    c_long,
                    c_ldword,
                )
                def _message_callback(
                    l_command,
                    l_login_id,
                    p_buf,
                    dw_buf_len,
                    pch_dvr_ip,
                    n_dvr_port,
                    b_alarm_ack_flag,
                    n_event_id,
                    dw_user,
                ) -> None:
                    self._handle_message(l_command, l_login_id, p_buf, dw_buf_len, n_event_id)

                self._message_callback = _message_callback
                sdk.SetDVRMessCallBackEx1(self._message_callback, 0)

            result = sdk.StartListenEx(self._login_id)
            if not result:
                raise DahuaConnectionError(sdk.GetLastErrorMessage())
            self._listening = True
            _LOGGER.debug("Dahua access listen started for %s", self.config.host)

    def stop_listening(self) -> None:
        """Stop push-event listening."""
        with self._lock:
            if self._sdk and self._listening and self._login_id:
                self._sdk.StopListen(self._login_id)
            self._listening = False

    def open_door(self, door_id: int, direction: str = "unknown") -> None:
        """Send a remote open command."""
        with self._lock:
            self.connect()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            param = structs.NET_CTRL_ACCESS_OPEN()
            param.dwSize = sizeof(structs.NET_CTRL_ACCESS_OPEN)
            param.nChannelID = int(door_id)
            param.emOpenDoorType = enums.EM_OPEN_DOOR_TYPE.EM_OPEN_DOOR_TYPE_REMOTE
            param.emOpenDoorDirection = {
                "enter": enums.EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_FROM_ENTER,
                "leave": enums.EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_FROM_LEAVE,
            }.get(direction, enums.EM_OPEN_DOOR_DIRECTION.EM_OPEN_DOOR_DIRECTION_UNKNOWN)
            param.szOperatorID = b"homeassistant"

            result = self._sdk.ControlDevice(self._login_id, enums.CtrlType.ACCESS_OPEN, param, 5000)
            if not result:
                raise DahuaConnectionError(self._sdk.GetLastErrorMessage())

    def get_user(self, user_id: str) -> AccessUser | None:
        """Fetch a user name from the controller by user id."""
        user_id = str(user_id).strip()
        if not user_id:
            return None
        with self._lock:
            self.connect()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            in_param = structs.NET_IN_ACCESS_USER_SERVICE_GET()
            in_param.dwSize = sizeof(structs.NET_IN_ACCESS_USER_SERVICE_GET)
            in_param.nUserNum = 1
            packed_user_ids = bytearray(3200)
            encoded = user_id.encode("utf-8")[:31]
            packed_user_ids[: len(encoded)] = encoded
            in_param.szUserID = bytes(packed_user_ids)

            users = (structs.NET_ACCESS_USER_INFO * 1)()
            fail_codes = (structs.C_ENUM * 1)()
            out_param = structs.NET_OUT_ACCESS_USER_SERVICE_GET()
            out_param.dwSize = sizeof(structs.NET_OUT_ACCESS_USER_SERVICE_GET)
            out_param.nMaxRetNum = 1
            out_param.pUserInfo = users
            out_param.pFailCode = fail_codes

            result = self._sdk.OperateAccessUserService(
                self._login_id,
                enums.EM_A_NET_EM_ACCESS_CTL_USER_SERVICE.NET_EM_ACCESS_CTL_USER_SERVICE_GET,
                in_param,
                out_param,
                5000,
            )
            if not result:
                _LOGGER.debug("Dahua user lookup failed for %s: %s", user_id, self._sdk.GetLastErrorMessage())
                return None

            info = users[0]
            resolved_id = _decode_bytes(info.szUserID) or user_id
            name = _decode_bytes(info.szNameEx) if bool(info.bUseNameEx) else ""
            name = name or _decode_bytes(info.szName)
            return AccessUser(
                user_id=resolved_id,
                name=name,
                status=int(info.nUserStatus),
                raw={"fail_code": int(fail_codes[0])},
            )

    def _ensure_sdk(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        try:
            from NetSDK import SDK_Callback as callbacks
            from NetSDK import SDK_Enum as enums
            from NetSDK import SDK_Struct as structs
            from NetSDK.NetSDK import NetClient
        except Exception as err:  # pragma: no cover - depends on native SDK install
            raise DahuaSdkUnavailable(
                "Dahua NetSDK Python bindings are not installed or native libraries cannot be loaded"
            ) from err

        sdk = NetClient()
        sdk.InitEx(None)
        self._sdk_modules = {"callbacks": callbacks, "enums": enums, "structs": structs}
        self._sdk = sdk
        return sdk

    def _build_device_info(self, raw_info: Any) -> AccessDeviceInfo:
        raw: dict[str, Any] = {}
        for field in ("sSerialNumber", "nChanNum", "nAlarmInPortNum", "nAlarmOutPortNum", "nDiskNum", "nDVRType"):
            if hasattr(raw_info, field):
                value = getattr(raw_info, field)
                raw[field] = _decode_bytes(value) if field.startswith("s") else int(value)
        serial = str(raw.get("sSerialNumber") or self.config.host)
        model = f"Dahua DVR type {raw['nDVRType']}" if raw.get("nDVRType") is not None else ""
        name = self._probe_device_name() or self.name_hint
        return AccessDeviceInfo(serial=serial, name=name, model=model, raw=raw)

    def _probe_device_name(self) -> str:
        """Try to read the controller name from Dahua config."""
        for command in ("General", "DeviceInfo", "SystemInfo"):
            buffer = create_string_buffer(512 * 1024)
            error = c_int(0)
            try:
                ok = bool(self._sdk.GetNewDevConfig(self._login_id, command, -1, buffer, len(buffer), error, 3000))
            except Exception:
                ok = False
            raw = bytes(buffer).split(b"\x00", 1)[0]
            if not ok and not raw:
                continue
            name = self._name_from_config(raw)
            if name:
                return name
        return ""

    def _handle_message(self, l_command: Any, l_login_id: Any, p_buf: Any, dw_buf_len: Any, n_event_id: Any) -> None:
        if not self._callback:
            return
        if self._login_id and self._handle_value(l_login_id) != self._handle_value(self._login_id):
            return

        enums = self._sdk_modules["enums"]
        structs = self._sdk_modules["structs"]
        command = int(l_command)
        try:
            if command == int(enums.SDK_ALARM_TYPE.ALARM_ACCESS_CTL_EVENT):
                info = cast(p_buf, POINTER(structs.NET_A_ALARM_ACCESS_CTL_EVENT_INFO)).contents
                event = AccessEvent(
                    kind="access_event",
                    result=normalize_access_result(info.bStatus, info.nErrorCode),
                    door_id=int(info.nDoor),
                    door_name=_decode_bytes(info.szDoorName) if hasattr(info, "szDoorName") else "",
                    reader_id=_decode_bytes(info.szReaderID),
                    method=normalize_open_method_name(enum_name(enums.EM_A_NET_ACCESS_DOOROPEN_METHOD, info.emOpenMethod)),
                    user_id=_decode_bytes(info.szUserID),
                    card_number=_decode_bytes(info.szCardNo),
                    card_name=_decode_bytes(info.szCardNameEx) or _decode_bytes(info.szCardName),
                    error_code=int(info.nErrorCode),
                    device_time=_sdk_time_to_string(info.stuTime),
                    raw_event_type=int(info.emEventType),
                    raw_status=int(info.bStatus),
                    raw={"event_type": normalize_event_type_name(enum_name(enums.EM_A_NET_ACCESS_CTL_EVENT_TYPE, info.emEventType))},
                )
                self._callback(event)
                return

            if command == int(enums.SDK_ALARM_TYPE.ALARM_ACCESS_CTL_STATUS):
                info = cast(p_buf, POINTER(structs.NET_A_ALARM_ACCESS_CTL_STATUS_INFO)).contents
                event = AccessEvent(
                    kind="access_status",
                    door_id=int(info.nDoor),
                    device_time=_sdk_time_to_string(info.stuTime),
                    raw_status=int(info.emStatus),
                    raw={
                        "door_status": normalize_door_status_name(
                            enum_name(enums.EM_A_NET_ACCESS_CTL_STATUS_TYPE, info.emStatus)
                        ),
                        "serial": _decode_bytes(info.szSerialNumber),
                    },
                )
                self._callback(event)
        except Exception:
            _LOGGER.exception("Failed to decode Dahua access event")

    @staticmethod
    def _is_handle(handle: Any) -> bool:
        return NetSDKAccessClient._handle_value(handle) != 0

    @staticmethod
    def _handle_value(handle: Any) -> int:
        return int(handle.value) if hasattr(handle, "value") else int(handle or 0)

    @staticmethod
    def _label_from_access_control_config(raw: bytes) -> str:
        if not raw:
            return ""
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return ""
        candidates = _walk_text_values(parsed)
        for key, value in candidates:
            key_lower = key.lower()
            if value and key_lower in {"name", "doorname", "channelname", "title"}:
                return value
        return ""

    @staticmethod
    def _name_from_config(raw: bytes) -> str:
        if not raw:
            return ""
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return ""
        for key, value in _walk_text_values(parsed):
            key_lower = key.lower()
            if value and key_lower in {"machinename", "devicename", "hostname", "name"}:
                return value
        return ""


def _walk_text_values(value: Any, parent_key: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_walk_text_values(child, str(key)))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_text_values(child, parent_key))
    elif isinstance(value, str):
        found.append((parent_key, value.strip()))
    return found
