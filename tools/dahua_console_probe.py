#!/usr/bin/env python3
"""Console diagnostics for Dahua access controllers.

This is intentionally outside the Home Assistant integration. It gives us a
small, repeatable lab tool for connectivity tests before the HACS code starts.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ENV_FILE = Path("secrets/kd1_piwnica_kozla.env")


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


def redact(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[:1]}{'*' * (len(value) - 2)}{value[-1:]}"


def tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, float | None, str | None]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = (time.perf_counter() - started) * 1000
            return True, elapsed_ms, None
    except OSError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return False, elapsed_ms, str(exc)


def print_config(config: ControllerConfig, env_file: Path) -> None:
    print("DBS Dahua Access console probe")
    print(f"  env file:   {env_file}")
    print(f"  controller: {config.name}")
    print(f"  address:    {config.host}:{config.port}")
    print(f"  username:   {config.username}")
    print(f"  password:   {redact(config.password)}")
    print()


def run_probe(config: ControllerConfig, timeout: float) -> int:
    print(f"Checking TCP {config.host}:{config.port} ...")
    ok, elapsed_ms, error = tcp_probe(config.host, config.port, timeout)

    if ok:
        print(f"  status: OPEN ({elapsed_ms:.0f} ms)")
        print("  next:   NetSDK login test can be added after the SDK/runtime is selected.")
        return 0

    print(f"  status: CLOSED/UNREACHABLE ({elapsed_ms:.0f} ms)")
    print(f"  error:  {error}")
    return 2


def run_watch(config: ControllerConfig, timeout: float, interval: float) -> int:
    print(f"Watching TCP {config.host}:{config.port}; press Ctrl+C to stop.")
    try:
        while True:
            ok, elapsed_ms, error = tcp_probe(config.host, config.port, timeout)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if ok:
                print(f"{timestamp} OPEN {elapsed_ms:.0f} ms")
            else:
                print(f"{timestamp} DOWN {elapsed_ms:.0f} ms {error}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Console diagnostics for Dahua access controllers.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Path to local controller env file, default: {DEFAULT_ENV_FILE}",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="TCP timeout in seconds.")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("probe", help="Run a one-shot TCP probe.")

    watch_parser = subparsers.add_parser("watch", help="Continuously watch TCP availability.")
    watch_parser.add_argument("--interval", type=float, default=5.0, help="Delay between checks in seconds.")

    parser.set_defaults(command="probe")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    env_file = args.env_file

    try:
        config = load_config(env_file)
    except (OSError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    print_config(config, env_file)

    if args.command == "watch":
        return run_watch(config, args.timeout, args.interval)

    return run_probe(config, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
