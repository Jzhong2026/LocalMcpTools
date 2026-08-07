from __future__ import annotations

import pytest

from localmcptools.process.presets import PRESETS, UnknownPreset, resolve


def test_required_presets_resolve_to_argv() -> None:
    assert set(PRESETS) == {"python-uvicorn", "node-vite", "node-next-dev", "dotnet-run"}
    _preset, argv = resolve("python-uvicorn", ["app:app", "--port", "8123"])
    assert argv[-1] == "--reload"
    assert "uvicorn" in argv
    _preset, argv = resolve("dotnet-run", ["api.csproj"])
    assert argv[-1] == "api.csproj"
    _preset, argv = resolve("node-vite", ["--port", "8123"])
    assert argv == ("npx", "vite", "--port", "8123")
    _preset, argv = resolve("node-next-dev", ["--port", "8124"])
    assert argv == ("npx", "next", "dev", "--port", "8124")


def test_unknown_preset_is_typed() -> None:
    with pytest.raises(UnknownPreset):
        resolve("arbitrary-shell", [])


def test_preset_args_are_a_string_array() -> None:
    with pytest.raises(ValueError):
        resolve("node-vite", "--port 8000")
