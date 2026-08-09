#!/usr/bin/env python3
"""Print the current private LAN address, if macOS exposes one."""

import ipaddress
import socket
import subprocess


def _command(*arguments: str) -> str:
    result = subprocess.run(arguments, capture_output=True, check=False, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _private_address(candidate: str) -> str | None:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if address.version == 4 and address.is_private and not address.is_loopback:
        return str(address)
    return None


def discover_lan_ip() -> str | None:
    route = _command("route", "-n", "get", "default")
    for line in route.splitlines():
        if line.strip().startswith("interface:"):
            interface = line.split(":", 1)[1].strip()
            address = _private_address(_command("ipconfig", "getifaddr", interface))
            if address:
                return address

    for interface in ("en0", "en1"):
        address = _private_address(_command("ipconfig", "getifaddr", interface))
        if address:
            return address

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("192.0.2.1", 80))
            return _private_address(connection.getsockname()[0])
    except OSError:
        return None


def main() -> None:
    address = discover_lan_ip()
    if address:
        print(address)


if __name__ == "__main__":
    main()
