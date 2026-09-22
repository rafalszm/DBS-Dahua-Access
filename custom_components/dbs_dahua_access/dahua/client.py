"""Dahua NetSDK access-controller client."""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from collections.abc import Callable
from ctypes import POINTER, c_char, c_char_p, c_int, c_long, c_void_p, cast, create_string_buffer, pointer, sizeof
from typing import Any, Protocol

from .exceptions import DahuaAuthError, DahuaConnectionError, DahuaSdkUnavailable
from .models import AccessCard, AccessControllerConfig, AccessDeviceInfo, AccessDoor, AccessEvent, AccessUser
from .normalizer import (
    door_id_to_sdk_channel,
    enum_name,
    normalize_access_result,
    normalize_door_status_name,
    normalize_event_type_name,
    normalize_open_method_name,
    resolve_door_label,
    sdk_channel_to_door_id,
    sdk_device_class_name,
)
from .vendor_loader import ensure_netsdk_available

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

    def open_door(self, sdk_channel: int, direction: str = "unknown") -> None:
        """Send a remote open command to a zero-based SDK channel."""

    def get_user(self, user_id: str) -> AccessUser | None:
        """Fetch a user by access-control user id."""

    def get_users(self, user_ids: list[str]) -> list[AccessUser]:
        """Fetch access-control users in one SDK request."""

    def list_cards(self) -> list[AccessCard]:
        """Return card credentials known by the controller."""

    def get_user_by_card(self, card_number: str) -> AccessUser | None:
        """Resolve a controller user from a card number."""


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
        self._door_config_cache: dict[int, bytes] = {}

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
        """Ask the controller for its door/passage count and return doors."""
        self.connect()
        doors = self._doors_from_subcontrollers()
        if doors:
            return doors

        door_count, count_source = self._discover_door_count()
        if door_count is None:
            _LOGGER.warning(
                "Dahua controller %s did not report an authoritative door count; no door entities will be created",
                self.config.host,
            )
            return []

        doors = []
        for door_id in range(1, max(1, min(door_count, 64)) + 1):
            channel = door_id_to_sdk_channel(door_id)
            raw = self._door_config_cache.get(channel)
            if raw is None:
                raw = self._get_new_config("AccessControl", channel)
            label = self._label_from_access_control_config(raw) or resolve_door_label(door_id)
            source = "config" if raw else count_source
            doors.append(AccessDoor(door_id=door_id, label=label, sdk_channel=channel, source=source))
        return doors

    def _discover_door_count(self) -> tuple[int | None, str]:
        subcontrollers = self._get_subcontrollers()
        count = self._door_count_from_subcontrollers(subcontrollers)
        if count:
            return count, "subcontroller_info"
        count = self._door_count_from_access_control_general()
        if count:
            return count, "access_control_general"
        count = self._door_count_from_access_control_channels()
        if count:
            return count, "access_control_channels"
        return None, "unknown"

    def _doors_from_subcontrollers(self) -> list[AccessDoor]:
        subcontrollers = self._get_subcontrollers()
        if not subcontrollers:
            return []

        doors: dict[int, AccessDoor] = {}
        next_door_id = 1
        for item in subcontrollers:
            subcontroller_name = _decode_bytes(getattr(item, "szSubControllerName", b""))
            door_count = int(getattr(item, "nDoorNum", 0))
            item_reader_doors: list[int] = []
            for reader_index in range(min(max(door_count, 0), 128)):
                reader = item.stuReaderInfo[reader_index]
                sdk_channel = int(getattr(reader, "nDoor", -1))
                if not 0 <= sdk_channel < 64:
                    continue
                door_id = sdk_channel_to_door_id(sdk_channel)
                label = self._door_label_from_subcontroller(subcontroller_name, door_id, door_count)
                doors[door_id] = AccessDoor(
                    door_id=door_id,
                    label=label,
                    sdk_channel=sdk_channel,
                    source="subcontroller_info",
                )
                item_reader_doors.append(door_id)
                next_door_id = max(next_door_id, door_id + 1)

            if door_count and not item_reader_doors:
                start = next_door_id
                for door_id in range(start, min(start + door_count, 65)):
                    label = self._door_label_from_subcontroller(subcontroller_name, door_id, door_count)
                    doors[door_id] = AccessDoor(
                        door_id=door_id,
                        label=label,
                        sdk_channel=door_id_to_sdk_channel(door_id),
                        source="subcontroller_info",
                    )
                    next_door_id = max(next_door_id, door_id + 1)

        return [doors[door_id] for door_id in sorted(doors)]

    def _get_subcontrollers(self) -> list[Any]:
        structs = self._sdk_modules["structs"]
        enums = self._sdk_modules["enums"]
        if not all(
            hasattr(structs, name)
            for name in ("NET_IN_GET_SUB_CONTROLLER_INFO", "NET_OUT_GET_SUB_CONTROLLER_INFO")
        ):
            return []

        in_param = structs.NET_IN_GET_SUB_CONTROLLER_INFO()
        in_param.dwSize = sizeof(structs.NET_IN_GET_SUB_CONTROLLER_INFO)
        in_param.nSubControllerID[0] = -1
        in_param.nSubControllerNum = 1

        out_param = structs.NET_OUT_GET_SUB_CONTROLLER_INFO()
        out_param.dwSize = sizeof(structs.NET_OUT_GET_SUB_CONTROLLER_INFO)

        ok = bool(
            self._sdk.OperateAccessControlManager(
                self._login_id,
                enums.EM_A_NET_EM_ACCESS_CTL_MANAGER.NET_EM_ACCESS_CTL_GETSUBCONTROLLER_INFO,
                in_param,
                out_param,
                5000,
            )
        )
        if not ok:
            _LOGGER.debug("Dahua subcontroller door-count query failed: %s", self._sdk.GetLastErrorMessage())
            return []

        returned = max(0, min(int(out_param.nRetNum), 64))
        if returned == 0:
            return []
        return [out_param.stuSubControllerInfo[index] for index in range(returned)]

    def _door_count_from_subcontrollers(self, subcontrollers: list[Any]) -> int | None:
        if not subcontrollers:
            return None

        explicit_counts: list[int] = []
        reader_door_ids: list[int] = []
        for item in subcontrollers:
            door_count = int(getattr(item, "nDoorNum", 0))
            if 0 < door_count <= 64:
                explicit_counts.append(door_count)
            for reader_index in range(min(max(door_count, 0), 128)):
                reader = item.stuReaderInfo[reader_index]
                door_id = int(getattr(reader, "nDoor", 0))
                if 0 < door_id <= 64:
                    reader_door_ids.append(door_id)

        if reader_door_ids:
            return max(reader_door_ids) + 1
        if len(explicit_counts) == 1:
            return explicit_counts[0]
        if explicit_counts:
            return sum(explicit_counts)
        return None

    @staticmethod
    def _door_label_from_subcontroller(subcontroller_name: str, door_id: int, door_count: int) -> str:
        name = subcontroller_name.strip()
        if not name:
            return resolve_door_label(door_id)
        if door_count <= 1:
            return name
        return f"{name} {door_id}"

    def _door_count_from_access_control_general(self) -> int | None:
        for channel in (-1, 0):
            buffer = create_string_buffer(512 * 1024)
            error = c_int(0)
            ok = bool(
                self._sdk.GetNewDevConfig(
                    self._login_id,
                    "AccessControlGeneral",
                    channel,
                    buffer,
                    len(buffer),
                    error,
                    3000,
                )
            )
            raw = bytes(buffer).split(b"\x00", 1)[0]
            if not ok and not raw:
                continue
            count = self._door_count_from_config(raw)
            if count:
                return count
        return None

    def _door_count_from_access_control_channels(self) -> int | None:
        """Count only channels explicitly accepted and echoed by the controller."""
        count = 0
        self._door_config_cache.clear()
        for channel in range(64):
            raw = self._get_new_config("AccessControl", channel)
            if not self._config_confirms_channel(raw, channel):
                break
            self._door_config_cache[channel] = raw
            count += 1
        return count or None

    def _get_new_config(self, command: str, channel: int) -> bytes:
        buffer = create_string_buffer(512 * 1024)
        error = c_int(0)
        try:
            ok = bool(
                self._sdk.GetNewDevConfig(
                    self._login_id,
                    command,
                    channel,
                    buffer,
                    len(buffer),
                    error,
                    3000,
                )
            )
        except Exception:
            return b""
        raw = bytes(buffer).split(b"\x00", 1)[0]
        return raw if ok else b""

    @staticmethod
    def _config_confirms_channel(raw: bytes, requested_channel: int) -> bool:
        if not raw:
            return False
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed, dict) or parsed.get("result") is not True:
            return False
        params = parsed.get("params")
        if not isinstance(params, dict):
            return False
        try:
            returned_channel = int(params.get("channel"))
        except (TypeError, ValueError):
            return False
        return returned_channel == requested_channel

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

    def open_door(self, sdk_channel: int, direction: str = "unknown") -> None:
        """Send a remote open command to a zero-based SDK channel."""
        with self._lock:
            self.connect()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            param = structs.NET_CTRL_ACCESS_OPEN()
            param.dwSize = sizeof(structs.NET_CTRL_ACCESS_OPEN)
            param.nChannelID = int(sdk_channel)
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
        users = self.get_users([user_id])
        return users[0] if users else None

    def get_users(self, user_ids: list[str]) -> list[AccessUser]:
        """Fetch up to 100 users in one native SDK request."""
        clean_ids = [value for value in dict.fromkeys(str(item).strip() for item in user_ids) if value][:100]
        if not clean_ids:
            return []
        with self._lock:
            self.connect()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            in_param = structs.NET_IN_ACCESS_USER_SERVICE_GET()
            in_param.dwSize = sizeof(structs.NET_IN_ACCESS_USER_SERVICE_GET)
            in_param.nUserNum = len(clean_ids)
            packed_user_ids = bytearray(3200)
            for index, user_id in enumerate(clean_ids):
                encoded = user_id.encode("utf-8")[:31]
                offset = index * 32
                packed_user_ids[offset : offset + len(encoded)] = encoded
            in_param.szUserID = bytes(packed_user_ids)

            users = (structs.NET_ACCESS_USER_INFO * len(clean_ids))()
            fail_codes = (structs.C_ENUM * len(clean_ids))()
            out_param = structs.NET_OUT_ACCESS_USER_SERVICE_GET()
            out_param.dwSize = sizeof(structs.NET_OUT_ACCESS_USER_SERVICE_GET)
            out_param.nMaxRetNum = len(clean_ids)
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
                _LOGGER.debug("Dahua user lookup failed: %s", self._sdk.GetLastErrorMessage())
                if len(clean_ids) > 1:
                    resolved_one_by_one: list[AccessUser] = []
                    for user_id in clean_ids:
                        resolved_one_by_one.extend(self.get_users([user_id]))
                    return resolved_one_by_one
                return []

            resolved: list[AccessUser] = []
            for index, requested_id in enumerate(clean_ids):
                info = users[index]
                resolved_id = _decode_bytes(info.szUserID) or requested_id
                name = _decode_bytes(info.szNameEx) if bool(info.bUseNameEx) else ""
                name = name or _decode_bytes(info.szName)
                fail_code = int(fail_codes[index])
                if not name and not _decode_bytes(info.szUserID) and fail_code:
                    continue
                resolved.append(
                    AccessUser(
                        user_id=resolved_id,
                        name=name,
                        status=int(info.nUserStatus),
                        raw={"fail_code": fail_code},
                    )
                )
            return resolved

    def list_cards(self) -> list[AccessCard]:
        """Enumerate access cards, then enrich their user IDs via the card service."""
        with self._lock:
            self.connect()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]

            condition = structs.NET_A_FIND_RECORD_ACCESSCTLCARD_CONDITION()
            condition.dwSize = sizeof(condition)
            in_find = structs.NET_IN_FIND_RECORD_PARAM()
            in_find.dwSize = sizeof(in_find)
            in_find.emType = enums.EM_NET_RECORD_TYPE.ACCESSCTLCARD
            in_find.pQueryCondition = cast(pointer(condition), c_void_p)
            out_find = structs.NET_OUT_FIND_RECORD_PARAM()
            out_find.dwSize = sizeof(out_find)

            if not self._sdk.FindRecord(self._login_id, in_find, out_find, 5000):
                _LOGGER.debug("Dahua card enumeration is unavailable: %s", self._sdk.GetLastErrorMessage())
                return []

            cards: dict[str, AccessCard] = {}
            handle = out_find.lFindeHandle
            try:
                batch_size = 16
                while len(cards) < 2000:
                    records = (structs.NET_RECORDSET_ACCESS_CTL_CARD * batch_size)()
                    for record in records:
                        record.dwSize = sizeof(structs.NET_RECORDSET_ACCESS_CTL_CARD)
                    in_next = structs.NET_IN_FIND_NEXT_RECORD_PARAM()
                    in_next.dwSize = sizeof(in_next)
                    in_next.lFindeHandle = handle
                    in_next.nFileCount = batch_size
                    out_next = structs.NET_OUT_FIND_NEXT_RECORD_PARAM()
                    out_next.dwSize = sizeof(out_next)
                    out_next.pRecordList = cast(records, c_void_p)
                    out_next.nMaxRecordNum = batch_size
                    if not self._sdk.FindNextRecord(in_next, out_next, 5000):
                        break
                    returned = max(0, min(int(out_next.nRetRecordNum), batch_size))
                    for index in range(returned):
                        record = records[index]
                        card_number = _decode_bytes(record.szCardNo)
                        if not card_number:
                            continue
                        cards[card_number] = AccessCard(
                            card_number=card_number,
                            user_id=_decode_bytes(record.szUserID),
                            name=_decode_bytes(record.szCardName),
                            status=int(record.emStatus),
                        )
                    if returned < batch_size:
                        break
            finally:
                self._sdk.FindRecordClose(handle)

            card_numbers = list(cards)
            for offset in range(0, len(card_numbers), 100):
                for binding in self._get_cards_by_number(card_numbers[offset : offset + 100]):
                    previous = cards.get(binding.card_number)
                    cards[binding.card_number] = AccessCard(
                        card_number=binding.card_number,
                        user_id=binding.user_id or (previous.user_id if previous else ""),
                        name=previous.name if previous else "",
                        status=binding.status if binding.status is not None else (previous.status if previous else None),
                    )
            return list(cards.values())

    def get_user_by_card(self, card_number: str) -> AccessUser | None:
        """Resolve a user through the native card service."""
        bindings = self._get_cards_by_number([card_number])
        if not bindings or not bindings[0].user_id:
            return None
        return self.get_user(bindings[0].user_id)

    def _get_cards_by_number(self, card_numbers: list[str]) -> list[AccessCard]:
        clean_numbers = [value for value in dict.fromkeys(str(item).strip() for item in card_numbers) if value][:100]
        if not clean_numbers:
            return []
        with self._lock:
            self.connect()
            structs = self._sdk_modules["structs"]
            enums = self._sdk_modules["enums"]
            in_param = structs.NET_IN_ACCESS_CARD_SERVICE_GET()
            in_param.dwSize = sizeof(in_param)
            in_param.nCardNum = len(clean_numbers)
            packed_card_numbers = bytearray(3200)
            for index, card_number in enumerate(clean_numbers):
                encoded = card_number.encode("utf-8")[:31]
                offset = index * 32
                packed_card_numbers[offset : offset + len(encoded)] = encoded
            in_param.szCardNo = bytes(packed_card_numbers)

            card_info = (structs.NET_ACCESS_CARD_INFO * len(clean_numbers))()
            fail_codes = (structs.C_ENUM * len(clean_numbers))()
            out_param = structs.NET_OUT_ACCESS_CARD_SERVICE_GET()
            out_param.dwSize = sizeof(out_param)
            out_param.nMaxRetNum = len(clean_numbers)
            out_param.pCardInfo = card_info
            out_param.pFailCode = fail_codes
            result = self._sdk.OperateAccessCardService(
                self._login_id,
                enums.EM_A_NET_EM_ACCESS_CTL_CARD_SERVICE.NET_EM_ACCESS_CTL_CARD_SERVICE_GET,
                in_param,
                out_param,
                5000,
            )
            if not result:
                return []

            bindings: list[AccessCard] = []
            for index, requested_number in enumerate(clean_numbers):
                info = card_info[index]
                card_number = _decode_bytes(info.szCardNo) or requested_number
                user_id = _decode_bytes(info.szUserIDEx) if bool(info.bUserIDEx) else ""
                user_id = user_id or _decode_bytes(info.szUserID)
                if not user_id and int(fail_codes[index]):
                    continue
                bindings.append(
                    AccessCard(
                        card_number=card_number,
                        user_id=user_id,
                        status=int(info.nCardStatus),
                    )
                )
            return bindings

    def _ensure_sdk(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        try:
            ensure_netsdk_available()
            from NetSDK import SDK_Callback as callbacks
            from NetSDK import SDK_Enum as enums
            from NetSDK import SDK_Struct as structs
            from NetSDK.NetSDK import NetClient
        except DahuaSdkUnavailable:
            raise
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
        metadata = self._probe_software_info()
        device_class = sdk_device_class_name(raw.get("nDVRType"))
        raw["sdk_device_class"] = device_class
        raw.update({key: value for key, value in metadata.items() if value})
        model = metadata.get("detail_type") or metadata.get("device_type") or device_class
        name = self._probe_device_name() or self.name_hint
        return AccessDeviceInfo(
            serial=serial,
            name=name,
            model=model,
            firmware=metadata.get("firmware", ""),
            hardware=metadata.get("hardware", ""),
            raw=raw,
        )

    def _probe_software_info(self) -> dict[str, str]:
        """Read real product and version metadata through the SDK state query."""
        structs = self._sdk_modules["structs"]
        enums = self._sdk_modules["enums"]
        if not hasattr(structs, "NET_A_DEV_VERSION_INFO"):
            return {}

        info = structs.NET_A_DEV_VERSION_INFO()
        try:
            ok = bool(
                self._sdk.QueryDevState(
                    self._login_id,
                    enums.EM_QUERY_DEV_STATE_TYPE.SOFTWARE,
                    info,
                    sizeof(info),
                    0,
                    5000,
                )
            )
        except Exception:
            _LOGGER.debug("Dahua software metadata query failed", exc_info=True)
            return {}
        if not ok:
            return {}

        return {
            "device_type": _decode_bytes(info.szDevType),
            "detail_type": _decode_bytes(info.szDetailType),
            "firmware": _decode_bytes(info.szSoftWareVersion),
            "hardware": _decode_bytes(info.szHardwareVersion),
        }

    def _probe_device_name(self) -> str:
        """Try to read the controller name from Dahua config."""
        for channel in (-1, 0):
            buffer = create_string_buffer(512 * 1024)
            error = c_int(0)
            try:
                ok = bool(
                    self._sdk.GetNewDevConfig(
                        self._login_id,
                        "General",
                        channel,
                        buffer,
                        len(buffer),
                        error,
                        3000,
                    )
                )
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
                sdk_channel = int(info.nDoor)
                event = AccessEvent(
                    kind="access_event",
                    result=normalize_access_result(info.bStatus, info.nErrorCode),
                    door_id=sdk_channel_to_door_id(sdk_channel),
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
                    raw={
                        "event_type": normalize_event_type_name(
                            enum_name(enums.EM_A_NET_ACCESS_CTL_EVENT_TYPE, info.emEventType)
                        ),
                        "sdk_channel": sdk_channel,
                        "pin_present": bool(_decode_bytes(info.szPwd)),
                    },
                )
                self._callback(event)
                return

            if command == int(enums.SDK_ALARM_TYPE.ALARM_ACCESS_CTL_STATUS):
                info = cast(p_buf, POINTER(structs.NET_A_ALARM_ACCESS_CTL_STATUS_INFO)).contents
                sdk_channel = int(info.nDoor)
                event = AccessEvent(
                    kind="access_status",
                    door_id=sdk_channel_to_door_id(sdk_channel),
                    device_time=_sdk_time_to_string(info.stuTime),
                    raw_status=int(info.emStatus),
                    raw={
                        "door_status": normalize_door_status_name(
                            enum_name(enums.EM_A_NET_ACCESS_CTL_STATUS_TYPE, info.emStatus)
                        ),
                        "serial": _decode_bytes(info.szSerialNumber),
                        "sdk_channel": sdk_channel,
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
    def _door_count_from_config(raw: bytes) -> int | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        for key, value in _walk_values(parsed):
            key_lower = key.lower()
            if key_lower in {"doorcount", "doorcnt", "doornum", "ndoorcount", "ndoornum"}:
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 < count <= 64:
                    return count

        return None

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
            if value and key_lower in {"machinename", "szmachinename"}:
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


def _walk_values(value: Any, parent_key: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_walk_values(child, str(key)))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_values(child, parent_key))
    else:
        found.append((parent_key, value))
    return found
