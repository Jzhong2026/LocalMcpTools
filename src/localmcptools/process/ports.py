"""Read-only TCP listener inspection."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import psutil

from . import manager


@dataclass(frozen=True)
class PortInfo:
    port: int
    address: str
    protocol: str
    pid: int | None
    managed_id: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def list_listening_ports() -> list[PortInfo]:
    managed_by_pid: dict[int, manager.ManagedProcess] = {}
    for item in manager.all_running():
        managed_by_pid[item.pid] = item
        try:
            for child in psutil.Process(item.pid).children(recursive=True):
                managed_by_pid[child.pid] = item
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    found: list[PortInfo] = []
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue
        pid = connection.pid
        managed = managed_by_pid.get(pid) if pid is not None else None
        found.append(PortInfo(
            port=int(connection.laddr.port), address=str(connection.laddr.ip),
            protocol="tcp", pid=pid, managed_id=managed.id if managed else None,
        ))
    return sorted(found, key=lambda item: (item.port, item.address, item.pid or -1))


def find_by_port(port: int) -> PortInfo | None:
    return next((item for item in list_listening_ports() if item.port == port), None)


__all__ = ["PortInfo", "find_by_port", "list_listening_ports"]
