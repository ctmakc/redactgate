"""In-process network kill switch.

The desktop app only ever talks to itself over 127.0.0.1 (the UI window <-> the local
server). :func:`install` wraps the socket layer so any attempt to connect anywhere else —
by our code or by a library — fails with ``PermissionError`` and is counted, and DNS
lookups for non-local names are refused too. The self-check screen shows the counters.
"""

from __future__ import annotations

import ipaddress
import socket
import threading

_lock = threading.Lock()
_state = {"installed": False, "blocked": 0, "last_blocked": None}
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "ip6-localhost", ""}


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).strip("[]").lower()
    if host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _blocked(target) -> PermissionError:
    with _lock:
        _state["blocked"] += 1
        _state["last_blocked"] = str(target)[:80]
    return PermissionError(f"RedactGate Desktop is offline-only: outbound connection to {target!r} blocked")


def install() -> None:
    if _state["installed"]:
        return
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_getaddrinfo = socket.getaddrinfo
    orig_sendto = socket.socket.sendto

    def _addr_host(address):
        if isinstance(address, tuple) and address:
            return address[0]
        return None  # AF_UNIX paths etc. are local

    def connect(self, address):  # type: ignore[no-untyped-def]
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(_addr_host(address)):
            raise _blocked(address)
        return orig_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(_addr_host(address)):
            raise _blocked(address)
        return orig_connect_ex(self, address)

    def sendto(self, data, *args):  # type: ignore[no-untyped-def]
        address = args[-1] if args else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(_addr_host(address)):
            raise _blocked(address)
        return orig_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not _is_local(host):
            raise _blocked(host)
        return orig_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.socket.sendto = sendto  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    _state["installed"] = True


def status() -> dict:
    with _lock:
        return dict(_state)
