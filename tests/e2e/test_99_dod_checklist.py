"""DoD coverage framework (test_99).

The plan (``docs/e2e-plan.md`` § 7.15) calls for a single parametric
test that loops over each OpenSpec change's DoD items and asserts the
corresponding e2e test exists and has been ticked. This module:

1. Imports the full :data:`dod_registry.REGISTRY`.
2. For every entry with ``status == covered``, asks pytest's
   collection to confirm the named test id actually exists. If a
   test id was renamed or removed, the framework **errors** (not
   fails) so the coverage gap is loud but doesn't break unrelated
   work.
3. Prints a coverage report to pytest's terminal so CI logs show
   ``covered / pending / deferred`` per change.
4. Surfaces ``unregistered_unchecked`` items — tasks.md boxes that
   the framework doesn't track yet. Today this is informational; once
   the backlog is empty the framework starts failing on new gaps.

Run with:

    pytest tests/e2e/test_99_dod_checklist.py -v -s

The output line per change (``[change] covered=.. pending=.. deferred=..``)
is what CI dashboards should scrape.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from .dod_registry import (
    REGISTRY,
    DoDEntry,
    coverage_summary,
    unregistered_unchecked,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_ids_in_entry(entry: DoDEntry) -> Iterator[str]:
    yield entry.test_id or ""


@pytest.fixture(scope="module")
def collected_test_ids() -> set[str]:
    """Run pytest's own collector against the e2e suite and return
    the set of fully-qualified test node ids.

    Uses ``--override-ini="addopts="`` to clear the parent's
    ``-m not e2e`` (which would otherwise cancel our ``-m e2e``).
    """
    import subprocess
    import sys
    from pathlib import Path

    here = Path(__file__).resolve()
    repo_root = str(here.parent.parent.parent)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e",
            "-m",
            "e2e",
            "--collect-only",
            "-q",
            "--override-ini=addopts=",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    ids: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" in line and not line.startswith(("=", "_")):
            ids.add(line)
    return ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_covered_entries_point_at_real_tests(collected_test_ids: set[str]) -> None:
    """Every ``status == covered`` entry must reference a test that
    pytest actually collected. If this fails, somebody renamed the
    test without updating :mod:`dod_registry`.
    """
    missing = [
        entry
        for entry in REGISTRY
        if entry.status == "covered"
        and entry.test_id not in collected_test_ids
    ]
    if missing:
        names = "\n".join(f"  - {e.test_id}  [{e.change} §{e.section}]" for e in missing)
        pytest.fail(
            f"{len(missing)} DoD entries point at tests that no longer exist:\n"
            f"{names}\n\nUpdate dod_registry.py or restore the missing tests."
        )


def test_registry_is_well_formed() -> None:
    """Every covered entry has a ``test_id``; every deferred entry has
    a ``reason``; no entry is empty.
    """
    errors: list[str] = []
    for entry in REGISTRY:
        if not entry.item.strip():
            errors.append(f"[{entry.change} §{entry.section}] empty item")
        if entry.status == "covered" and not entry.test_id:
            errors.append(
                f"[{entry.change} §{entry.section}] covered but no test_id: {entry.item!r}"
            )
        if entry.status == "deferred" and not entry.reason:
            errors.append(
                f"[{entry.change} §{entry.section}] deferred but no reason: {entry.item!r}"
            )
        if entry.status not in {"covered", "pending", "deferred"}:
            errors.append(
                f"[{entry.change} §{entry.section}] unknown status {entry.status!r}"
            )
    if errors:
        pytest.fail("registry is malformed:\n  - " + "\n  - ".join(errors))


def test_no_duplicate_items_per_change_section() -> None:
    """Within a (change, section) pair, item text must be unique so the
    framework never silently de-duplicates a DoD.
    """
    seen: dict[tuple[str, str], list[str]] = {}
    for entry in REGISTRY:
        key = (entry.change, entry.section)
        seen.setdefault(key, []).append(entry.item)
    dups = {k: v for k, v in seen.items() if len(v) != len(set(v))}
    if dups:
        msg = "\n".join(f"  {k}: {v!r}" for k, v in dups.items())
        pytest.fail(f"duplicate items:\n{msg}")


def test_unregistered_unchecked_is_documented() -> None:
    """Print every unchecked tasks.md box that the registry hasn't
    tracked yet. Today this is informational (``print`` only); once
    the backlog is empty we flip it to a fail.
    """
    gaps = unregistered_unchecked()
    summary = coverage_summary()
    print("\n=== DoD coverage report ===")
    print(f"{'change':<32} {'covered':>8} {'pending':>8} {'deferred':>9}")
    print("-" * 60)
    total = {"covered": 0, "pending": 0, "deferred": 0}
    for change in sorted(summary):
        s = summary[change]
        print(f"{change:<32} {s['covered']:>8} {s['pending']:>8} {s['deferred']:>9}")
        for k in total:
            total[k] += s[k]
    print("-" * 60)
    print(f"{'TOTAL':<32} {total['covered']:>8} {total['pending']:>8} {total['deferred']:>9}")
    if gaps:
        print(
            f"\n!! {len(gaps)} unchecked tasks.md boxes are not in the registry yet."
            "\nThey will become 'pending' entries as e2e tests land."
        )
        for change, section, item in gaps:
            print(f"  - [{change} §{section}] {item[:90]}")
    else:
        print("\nAll unchecked tasks.md boxes are tracked in the registry.")


# ---------------------------------------------------------------------------
# Per-change parametric tests
# ---------------------------------------------------------------------------
#
# We emit one named test per change so the coverage report shows up in
# pytest's test list. The test body asserts the change has at least
# one ``covered`` entry — i.e. something is hooked up.
# ---------------------------------------------------------------------------


_COVERED_COUNTS: dict[str, int] = {
    change: bucket["covered"]
    for change, bucket in coverage_summary().items()
}


@pytest.mark.parametrize("change", sorted(_COVERED_COUNTS))
def test_every_change_has_at_least_one_covered_entry(change: str) -> None:
    """Every change must have at least one e2e proof in the registry.

    If a new change lands and there's no covered entry yet, this test
    fails until somebody lands at least one e2e test for it.
    """
    count = _COVERED_COUNTS[change]
    assert count > 0, (
        f"change {change!r} has zero e2e coverage. "
        f"Add at least one 'covered' entry to dod_registry.REGISTRY."
    )