from __future__ import annotations

from types import SimpleNamespace

import psutil

from localmcptools.process import ports


def test_listening_ports_include_managed_association(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    listener = SimpleNamespace(
        status=psutil.CONN_LISTEN,
        laddr=SimpleNamespace(port=8123, ip="127.0.0.1"),
        pid=42,
    )
    unmanaged = SimpleNamespace(
        status=psutil.CONN_LISTEN,
        laddr=SimpleNamespace(port=8000, ip="0.0.0.0"),
        pid=43,
    )
    monkeypatch.setattr(ports.psutil, "net_connections", lambda kind: [listener, unmanaged])
    monkeypatch.setattr(
        ports.manager, "all_running", lambda: [SimpleNamespace(id="mp-one", pid=42)]
    )
    monkeypatch.setattr(
        ports.psutil, "Process", lambda pid: SimpleNamespace(children=lambda recursive: [])
    )
    result = ports.list_listening_ports()
    managed = next(item for item in result if item.port == 8123)
    assert managed.as_dict() == {
        "port": 8123, "address": "127.0.0.1", "protocol": "tcp",
        "pid": 42, "managed_id": "mp-one",
    }
    assert ports.find_by_port(8123) == managed
    assert ports.find_by_port(8000).managed_id is None  # type: ignore[union-attr]
