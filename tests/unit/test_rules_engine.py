"""Deny-rule loading, matching, reload safety and telemetry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localmcptools.persistence import db
from localmcptools.safety.rules import RuleEngine, load_all, record_hit


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "audit.sqlite"
    db.init_db(path)
    return path


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("Format-Volume -DriveLetter C", "block-format-volume"),
        ("cipher /w:C", "block-disk-wipe"),
        ("bcdedit /set {current} safeboot minimal", "block-boot-loader"),
        ("netsh advfirewall reset", "block-firewall-reset"),
        ("reg delete HKLM\\Software", "block-registry-delete"),
        ("net localgroup administrators user /add", "block-privilege-escalation"),
        ("taskkill /im lsass.exe", "block-kill-protected"),
        ("IEX (New-Object Net.WebClient).DownloadString('x')", "block-remote-download-exec"),
        ("reg add x /v fDenyTSConnections /d 0", "block-rdp-enable"),
    ],
)
def test_builtin_rules_match_dangerous_commands(command: str, expected: str) -> None:
    engine = RuleEngine()
    assert engine.reload()["errors"] == []
    hit = engine.match(command)
    assert hit is not None
    assert hit.rule_id == expected


def test_benign_command_does_not_match() -> None:
    engine = RuleEngine()
    engine.reload()
    assert engine.match("python -m pytest -q") is None


def test_reload_reports_bad_custom_rule_but_retains_valid_builtin_rules(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "bad.json").write_text("{ not json", encoding="utf-8")
    engine = RuleEngine(custom_dir=custom)
    report = engine.reload()
    assert report["reloaded"] == 10
    assert report["errors"]
    assert engine.match("diskpart") is not None


def test_hot_reload_picks_up_new_custom_rule(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    custom.mkdir()
    engine = RuleEngine(custom_dir=custom)
    engine.reload()
    assert engine.match("danger-tool") is None
    (custom / "custom.json").write_text(
        json.dumps(
            {
                "id": "custom-block",
                "severity": "high",
                "match": {"type": "any_of", "rules": [{"cmd_name": "danger-tool"}]},
            }
        ),
        encoding="utf-8",
    )
    assert engine.reload()["errors"] == []
    assert engine.match("danger-tool --now").rule_id == "custom-block"  # type: ignore[union-attr]


def test_rule_hits_accumulate_and_are_bounded(database: Path) -> None:
    with db.connection(database) as conn:
        record_hit("block-format-volume", "x" * 500, conn=conn)
        record_hit("block-format-volume", "diskpart", conn=conn)
        row = conn.execute(
            "SELECT * FROM rule_hit_stats WHERE rule_id = ?", ("block-format-volume",)
        ).fetchone()
    assert row["hit_count"] == 2
    assert row["last_hit_cmd"] == "diskpart"


def test_load_all_accepts_missing_custom_directory(tmp_path: Path) -> None:
    rules, errors = load_all(
        Path(__file__).parents[2] / "src" / "localmcptools" / "safety" / "builtin",
        tmp_path / "missing",
    )
    assert len(rules) == 10
    assert errors == []
