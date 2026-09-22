"""Load bundled Dahua NetSDK wheels for Home Assistant."""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import zipfile
from pathlib import Path

from .exceptions import DahuaSdkUnavailable

SDK_VERSION = "2.0.0.1"


def ensure_netsdk_available() -> None:
    """Make the bundled Dahua NetSDK importable.

    Home Assistant/HACS cannot install Dahua's private wheel from PyPI. The
    integration therefore vendors the official wheel files and extracts the one
    matching the running platform before importing ``NetSDK``.
    """
    try:
        import NetSDK  # noqa: F401

        return
    except Exception:
        pass

    wheel = _select_wheel()
    extract_dir = _runtime_dir(wheel)
    marker = extract_dir / ".dbs_dahua_access_extracted"
    if not marker.exists():
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(extract_dir)
        marker.write_text(wheel.name, encoding="utf-8")

    libs_dir = _libs_dir(extract_dir)
    if libs_dir is not None:
        _prepare_native_libs(libs_dir)

    path = str(extract_dir)
    if path not in sys.path:
        sys.path.insert(0, path)

    try:
        import NetSDK  # noqa: F401
    except Exception as err:
        raise DahuaSdkUnavailable(f"Dahua NetSDK could not be loaded from bundled wheel: {err}") from err


def _select_wheel() -> Path:
    system = platform.system().lower()
    machine = platform.machine().lower()
    is_64bit = sys.maxsize > 2**32

    if system == "linux" and machine in {"x86_64", "amd64"} and is_64bit:
        wheel_name = f"NetSDK-{SDK_VERSION}-py3-none-linux_x86_64.whl"
    elif system == "linux" and machine in {"i386", "i686", "x86"} and not is_64bit:
        wheel_name = f"NetSDK-{SDK_VERSION}-py3-none-linux_i686.whl"
    elif system == "windows" and machine in {"amd64", "x86_64"} and is_64bit:
        wheel_name = f"NetSDK-{SDK_VERSION}-py3-none-win_amd64.whl"
    elif system == "linux" and machine in {"aarch64", "arm64"}:
        raise DahuaSdkUnavailable(
            "Dahua NetSDK arm64/aarch64 wheel is not bundled. "
            "The currently available Dahua SDK package contains linux_x86_64, linux_i686 and win_amd64 only."
        )
    else:
        raise DahuaSdkUnavailable(f"Dahua NetSDK is not bundled for platform {system}/{machine}.")

    wheel = Path(__file__).resolve().parents[1] / "vendor" / "wheels" / wheel_name
    if not wheel.exists():
        raise DahuaSdkUnavailable(f"Bundled Dahua NetSDK wheel is missing: {wheel.name}")
    return wheel


def _runtime_dir(wheel: Path) -> Path:
    return wheel.parent.parent / "runtime" / wheel.stem


def _libs_dir(extract_dir: Path) -> Path | None:
    libs_root = extract_dir / "NetSDK" / "Libs"
    if not libs_root.exists():
        return None
    system = platform.system().lower()
    is_64bit = sys.maxsize > 2**32
    if system == "linux":
        return libs_root / ("linux64" if is_64bit else "linux32")
    if system == "windows":
        return libs_root / ("win64" if is_64bit else "win32")
    return None


def _prepare_native_libs(libs_dir: Path) -> None:
    if not libs_dir.exists():
        return
    os.environ["PATH"] = f"{libs_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["LD_LIBRARY_PATH"] = f"{libs_dir}{os.pathsep}{os.environ.get('LD_LIBRARY_PATH', '')}"

    if platform.system().lower() != "linux":
        return

    for name in (
        "libcrypto.so",
        "libssl.so",
        "libInfra.so",
        "libRenderEngine.so",
        "libStreamConvertor.so",
        "ImageAlg.so",
        "libavnetsdk.so",
        "libdhconfigsdk.so",
        "libplay.so",
    ):
        path = libs_dir / name
        if path.exists():
            try:
                ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                # Let the official wrapper raise the final actionable error.
                pass
