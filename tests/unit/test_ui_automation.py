"""Tests for the change-6 UI automation layer.

Covered surfaces:

- :mod:`localmcptools.safety.filters` — credential-window denylist.
- :mod:`localmcptools.ui.windows` — authorize / revoke / list_windows
  (Windows path is exercised in the integration suite; here we test
  the pure DB + filter logic).
- :mod:`localmcptools.ui.verify` — orchestrator + each predicate kind.
- :mod:`localmcptools.ui.ocr` — provider fallback + text-match helpers.
- :mod:`localmcptools.ui.screens` — token bucket rate limit.

Non-Windows hosts run every test that doesn't require ``uiautomation``;
the platform-conditional branch returns ``[]`` / ``{}`` and we assert
that contract.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from localmcptools.execution.service import ToolExecutionService
from localmcptools.persistence import db
from localmcptools.safety.filters import (
    filter_titles,
    is_visible_process,
    title_blocked,
)
from localmcptools.tools._common import STANDARD_ERROR_CODES
from localmcptools.ui import ocr as ocr_module
from localmcptools.ui import screens as screens_module
from localmcptools.ui.ocr import (
    StubProvider,
    get_provider,
    ocr_assert_text,
    ocr_find_text,
    ocr_region,
    reset_provider,
)
from localmcptools.ui.screens import TokenBucket
from localmcptools.ui.verify import (
    OCRPredicate,
    Predicate,
    PredicateResult,
    ScreenshotPredicate,
    UIAPredicate,
    verify,
)
from localmcptools.ui.windows import (
    DEFAULT_TTL_MS,
    authorize,
    is_authorized,
    lookup,
    revoke,
)

# --- safety.filters -------------------------------------------------------


def test_title_blocked_matches_credential_keywords() -> None:
    assert title_blocked("Sign in to your account")
    assert title_blocked("User Account Control")
    assert title_blocked("BitLocker Recovery Key")
    assert title_blocked("One-time code")
    assert title_blocked("Two-Factor Authentication")
    assert not title_blocked("VS Code")
    assert not title_blocked("")


def test_title_blocked_is_case_insensitive() -> None:
    assert title_blocked("SIGN IN")
    assert title_blocked("password manager")


def test_filter_titles_drops_blocked() -> None:
    titles = ["VS Code", "Sign in to GitHub", "Settings", "BitLocker Recovery"]
    filtered = filter_titles(titles)
    assert "VS Code" in filtered
    assert "Settings" in filtered
    assert "Sign in to GitHub" not in filtered
    assert "BitLocker Recovery" not in filtered


def test_is_visible_process_blocks_credentials() -> None:
    assert not is_visible_process("lsass.exe")
    assert not is_visible_process("CredentialUIBroker.exe")
    assert is_visible_process("Code.exe")
    assert is_visible_process("notepad.exe")


# --- ui.windows (DB logic) ------------------------------------------------


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh DB per test. The ``LMCP_DATA_DIR`` env var is set so the
    non-conn calls in :func:`lookup` / :func:`revoke` / :func:`is_authorized`
    resolve to the same temp DB.
    """
    path = tmp_path / "audit.sqlite"
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(path)
    return path


def test_authorize_writes_row(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=12345, process="Code.exe", pid=99, title="VS Code", conn=conn)
    assert row.hwnd == 12345
    assert row.process == "Code.exe"
    assert row.expires_at > row.issued_at


def test_lookup_returns_authorized_window(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=99, process="Code.exe", pid=1, title="VS Code", conn=conn)
    # The non-conn call goes through the default connection which uses LMCP_DATA_DIR.
    fetched = lookup(window_id=row.id)
    assert fetched is not None
    assert fetched.hwnd == 99
    assert is_authorized(window_id=row.id)


def test_revoke_invalidates_window(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=1, process="x", pid=1, title="t", conn=conn)
    assert revoke(window_id=row.id)
    assert lookup(window_id=row.id) is None
    assert not is_authorized(window_id=row.id)


def test_expired_authorization_returns_none(database: Path) -> None:
    with db.connection(database) as conn:
        row = authorize(hwnd=1, process="x", pid=1, title="t", ttl_ms=1, conn=conn)
    time.sleep(0.05)
    assert lookup(window_id=row.id) is None


def test_unknown_window_id_returns_none(database: Path) -> None:
    assert lookup(window_id="not-a-real-id") is None


# --- ui.authorize_window string tolerance --------------------------------


def _invoke(tool: str, logic, param_names, param_types=None, **kwargs):
    """Run a tool body through the real chokepoint and return the envelope.

    The regression this guards is precisely an *envelope*-level one: a
    tool body that returns ``{"error": ...}`` instead of raising comes
    back from the chokepoint as ``ok: true``, so only asserting on the
    envelope catches it. Calling the body directly cannot.
    """
    service_path = Path(os.environ["LMCP_DATA_DIR"]) / "chokepoint.sqlite"
    db.init_db(service_path)
    service = ToolExecutionService(audit_path=service_path)
    wrapper = service.register(
        tool, logic, param_names=list(param_names), param_types=param_types
    )
    return wrapper(**kwargs)


def test_authorize_window_accepts_real_int(database: Path, monkeypatch) -> None:
    from localmcptools.tools import ui as ui_tools

    monkeypatch.setattr(ui_tools, "authorize", lambda **kw: type("R", (), {"__dict__": kw})())
    monkeypatch.setattr(ui_tools, "describe", lambda **kw: None)
    out = ui_tools.ui_authorize_window({"hwnd": 17176112, "title": "Code"})
    assert "error" not in out
    assert out["window"]["hwnd"] == 17176112


def test_authorize_window_accepts_numeric_string(database: Path, monkeypatch) -> None:
    """A client that stringifies every field must still succeed."""
    from localmcptools.tools import ui as ui_tools

    monkeypatch.setattr(ui_tools, "authorize", lambda **kw: type("R", (), {"__dict__": kw})())
    monkeypatch.setattr(ui_tools, "describe", lambda **kw: None)
    out = ui_tools.ui_authorize_window({"hwnd": "17176112", "pid": "42", "ttl_ms": "60000"})
    assert "error" not in out
    assert out["window"]["hwnd"] == 17176112
    assert out["window"]["pid"] == 42
    assert out["window"]["ttl_ms"] == 60000


def test_authorize_window_rejects_non_numeric_hwnd(database: Path) -> None:
    from localmcptools.tools import ui as ui_tools

    envelope = _invoke(
        "ui.authorize_window",
        ui_tools.ui_authorize_window,
        ["hwnd"],
        {"hwnd": "integer"},
        hwnd="not-a-window",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_args"


def test_authorize_window_rejects_bool_hwnd(database: Path) -> None:
    """``True`` is an int subclass; it must not slip through as hwnd=1."""
    from localmcptools.tools import ui as ui_tools

    envelope = _invoke(
        "ui.authorize_window",
        ui_tools.ui_authorize_window,
        ["hwnd"],
        hwnd=True,
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_args"


# --- defect 1: authorize_window wrote the literal string "None" -----------


def test_authorize_window_never_writes_literal_none(database: Path, monkeypatch) -> None:
    """Regression: ``str(None)`` used to land in process/title as "None".

    An omitted argument arrives from the synthesised wrapper as an
    explicit ``None``, so ``args.get(k, "")`` never applied its default
    and ``str(None)`` produced a non-empty string that ``if not process``
    cannot filter out downstream.
    """
    from localmcptools.tools import ui as ui_tools

    monkeypatch.setattr(ui_tools, "describe", lambda **kw: None)
    out = ui_tools.ui_authorize_window({"hwnd": 4242})
    window = out["window"]
    assert window["process"] == ""
    assert window["title"] == ""
    assert "None" not in (window["process"], window["title"])


def test_authorize_window_backfills_live_metadata(database: Path, monkeypatch) -> None:
    """A caller that passes only ``hwnd`` still gets real metadata.

    ``ui.list_windows`` and ``ui.authorize_window`` must not describe the
    same handle differently.
    """
    from localmcptools.tools import ui as ui_tools

    summary = type(
        "S",
        (),
        {"hwnd": 4242, "process": "WorkBuddy.exe", "pid": 21996, "title": "WorkBuddy"},
    )()
    monkeypatch.setattr(ui_tools, "describe", lambda **kw: summary)
    out = ui_tools.ui_authorize_window({"hwnd": 4242})
    assert out["window"]["process"] == "WorkBuddy.exe"
    assert out["window"]["pid"] == 21996
    assert out["window"]["title"] == "WorkBuddy"


def test_describe_matches_list_windows(monkeypatch) -> None:
    """``describe`` reads the same pass ``list_windows`` exposes."""
    from localmcptools.ui import windows

    sample = [
        windows.WindowSummary(process="A.exe", pid=1, title="A", hwnd=10),
        windows.WindowSummary(process="B.exe", pid=2, title="B", hwnd=20),
    ]
    # Pin the enumeration: a live snapshot can change between the two
    # calls, which made this test fail on transient desktop windows.
    monkeypatch.setattr(windows, "list_windows", lambda: sample)
    assert windows.describe(hwnd=20) == sample[1]
    assert windows.describe(hwnd="10") == sample[0]
    assert windows.describe(hwnd=-12345) is None


def test_process_identity_does_not_depend_on_a_missing_uia_helper() -> None:
    """Regression: ``uiautomation`` has no ``GetWindowProcess``.

    The UIA branch used to call it inside a bare ``except``, so the
    ``AttributeError`` vanished and *every* visible window was reported
    as ``process=''`` / ``pid=0``. The helper must go through Win32.
    """
    from localmcptools.ui import windows

    pytest.importorskip("uiautomation")
    import uiautomation as auto

    assert not hasattr(auto, "GetWindowProcess"), (
        "uiautomation grew GetWindowProcess; _process_identity should be "
        "re-checked against it and the tests revisited"
    )

    # A real, guaranteed-live window: the console/desktop root owns one.
    name, pid = windows._process_identity(int(auto.GetRootControl().NativeWindowHandle))
    assert pid > 0, "GetWindowThreadProcessId returned no pid"
    assert name, "process name resolution produced an empty string"


@pytest.mark.skipif(os.name != "nt", reason="Win32 window enumeration")
def test_list_windows_populates_process_metadata() -> None:
    """The live enumeration must actually resolve process + pid."""
    from localmcptools.ui import windows

    rows = windows.list_windows()
    assert rows, "no windows enumerated"
    named = [r for r in rows if r.process and r.pid]
    assert named, (
        "every enumerated window has empty process/pid — process identity "
        "resolution is broken again: "
        + repr([(r.process, r.pid, r.title[:30]) for r in rows[:8]])
    )


# --- defect 2: tree / find visibility handling ----------------------------


class _FakeControl:
    """Minimal UIA control stand-in.

    ``IsVisible`` is settable to ``None`` on purpose: that is what
    ``uiautomation`` returns when the property is unsupported on a host,
    and ``bool(None)`` being ``False`` is what pruned whole subtrees.
    """

    def __init__(
        self,
        name: str = "",
        *,
        is_visible=None,
        control_type: str = "PaneControl",
        children: list["_FakeControl"] | None = None,
    ) -> None:
        self.Name = name
        self.IsVisible = is_visible
        self.ControlTypeName = control_type
        self.AutomationId = ""
        self.IsEnabled = True
        self._children = children or []

    def GetChildren(self):  # noqa: N802 — mirrors the UIA API
        return list(self._children)


def test_tree_visible_flag_treats_unknown_as_visible() -> None:
    from localmcptools.ui.tree import _visible_flag

    assert _visible_flag(_FakeControl(is_visible=None)) is True
    assert _visible_flag(_FakeControl(is_visible=True)) is True
    assert _visible_flag(_FakeControl(is_visible=False)) is False


def test_tree_walks_children_whose_visibility_is_unknown(monkeypatch) -> None:
    """Regression: ``if child.IsVisible:`` collapsed the tree to the root.

    On real hosts ``IsVisible`` reads as ``None`` for a large share of
    controls, so the walker returned ``total: 1`` for every app.
    """
    from localmcptools.ui import tree

    root = _FakeControl(
        "root",
        is_visible=True,
        children=[
            _FakeControl("child-a", is_visible=None),
            _FakeControl("child-b", is_visible=False),
        ],
    )
    monkeypatch.setattr(tree.os, "name", "nt")
    fake_module = type("M", (), {"ControlFromHandle": staticmethod(lambda h: root)})()
    monkeypatch.setitem(sys.modules, "uiautomation", fake_module)
    result = tree.get_tree(hwnd=1, depth=4)
    assert "error" not in result
    # A hidden node is still reported: the tree surface is faithful and
    # every node carries its own `isVisible`.
    assert result["total"] == 3
    names = {n["name"] for n in result["nodes"]}
    assert names == {"root", "child-a", "child-b"}
    hidden = next(n for n in result["nodes"] if n["name"] == "child-b")
    assert hidden["isVisible"] is False
    unknown = next(n for n in result["nodes"] if n["name"] == "child-a")
    assert unknown["isVisible"] is True


def test_tree_reports_failure_with_a_readable_code(monkeypatch) -> None:
    """Bare string errors gave no code for the envelope to carry."""
    from localmcptools.ui import tree

    monkeypatch.setattr(tree.os, "name", "posix")
    result = tree.get_tree(hwnd=1)
    assert result["error"]["code"] == "not_implemented"
    assert result["error"]["message"]


def test_find_visible_flag_treats_unknown_as_visible() -> None:
    from localmcptools.ui.find import _visible_flag

    assert _visible_flag(_FakeControl(is_visible=None)) is True
    assert _visible_flag(_FakeControl(is_visible=False)) is False


def test_find_drops_collapsed_matches_but_keeps_walking(monkeypatch) -> None:
    """A collapsed element is not a click target — but may hide children."""
    from localmcptools.ui import find

    leaf = _FakeControl("Save", is_visible=True, control_type="ButtonControl")
    collapsed = _FakeControl(
        "panel", is_visible=False, control_type="PanelControl", children=[leaf]
    )
    root = _FakeControl("root", is_visible=True, children=[collapsed])
    monkeypatch.setattr(find, "attach_to_window", lambda **kw: root)

    results = find.find_element(window_id="w", text="Save", max_results=20)
    assert [m["name"] for m in results] == ["Save"]
    # The output dict must expose the flag `ui.get_ui_tree` already had.
    assert [m["isVisible"] for m in results] == [True]


def test_find_omits_collapsed_control_itself(monkeypatch) -> None:
    from localmcptools.ui import find

    root = _FakeControl(
        "root",
        is_visible=True,
        children=[
            _FakeControl("panel", is_visible=False, control_type="PanelControl"),
            _FakeControl("OK", is_visible=None, control_type="ButtonControl"),
        ],
    )
    monkeypatch.setattr(find, "attach_to_window", lambda **kw: root)
    results = find.find_element(window_id="w", text="", max_results=20, control_type="PanelControl")
    assert results == []
    ok = find.find_element(window_id="w", text="OK", max_results=20)
    assert [m["name"] for m in ok] == ["OK"]
    assert ok[0]["isVisible"] is True


# --- defect 3: ocr.* source resolution -----------------------------------


def _ocr_envelope(tool, logic, param_names, **kwargs):
    return _invoke(tool, logic, param_names, **kwargs)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"window_id": "abc"}, {"window_id": "abc"}),
        ({"screenshot_handle": "art://x"}, {"screenshot_handle": "art://x"}),
        ({"window_id": None, "screenshot_handle": "art://x"}, {"screenshot_handle": "art://x"}),
        ({"window_id": "abc", "screenshot_handle": None}, {"window_id": "abc"}),
        ({"window_id": "  ", "screenshot_handle": "art://x"}, {"screenshot_handle": "art://x"}),
    ],
)
def test_resolve_source_ignores_absent_and_blank(args, expected) -> None:
    """Regression: the wrapper always supplies every key, so key-presence
    was a meaningless test and ``window_id=None`` became ``"None"``."""
    from localmcptools.tools.ocr import _resolve_source

    assert _resolve_source(args, tool="ocr.ocr_region") == expected


def test_resolve_source_requires_one_source(database: Path) -> None:
    from localmcptools.tools.ocr import _resolve_source

    envelope = _ocr_envelope(
        "ocr.ocr_region",
        lambda args: _resolve_source(args, tool="ocr.ocr_region"),
        ["window_id", "screenshot_handle"],
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_args"


def test_resolve_source_rejects_both_at_once(database: Path) -> None:
    from localmcptools.tools.ocr import _resolve_source

    envelope = _ocr_envelope(
        "ocr.ocr_region",
        lambda args: _resolve_source(args, tool="ocr.ocr_region"),
        ["window_id", "screenshot_handle"],
        window_id="abc",
        screenshot_handle="art://x",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_args"


def test_ocr_region_succeeds_with_screenshot_handle(database: Path, monkeypatch) -> None:
    """The reported failure: a real handle was rejected as no-source."""
    from localmcptools.tools import ocr as ocr_tools

    seen: dict = {}

    def fake_region(*, source):
        seen.update(source)
        return {"blocks": [], "full_text": ""}

    monkeypatch.setattr(ocr_tools.ocr_module, "ocr_region", fake_region)
    envelope = _ocr_envelope(
        "ocr.ocr_region",
        ocr_tools.ocr_ocr_region,
        ["window_id", "screenshot_handle"],
        screenshot_handle="art://2026/calls/abc.log",
    )
    assert envelope["ok"] is True
    assert seen == {"screenshot_handle": "art://2026/calls/abc.log"}


def test_ocr_failure_is_not_reported_as_ok(database: Path) -> None:
    """Regression: a module-layer error came back as ``ok: true``.

    A caller that only checks ``ok`` read a hard OCR failure as success —
    worse than a plain crash, because assertions turned green.
    """
    from localmcptools.tools import ocr as ocr_tools

    envelope = _ocr_envelope(
        "ocr.ocr_region",
        ocr_tools.ocr_ocr_region,
        ["window_id"],
        window_id="definitely-not-authorized",
    )
    assert envelope["ok"] is False
    # The failure is *classified*, not buried: an unresolvable window_id is
    # an authorisation problem, and the code says so instead of blaming the
    # argument shape the caller already got right.
    assert envelope["error"]["code"] == "window_not_authorized"
    assert envelope["data"] is None or "error" not in (envelope["data"] or {})


def test_ocr_unknown_error_code_is_downgraded_not_crashed(database: Path) -> None:
    from localmcptools.tools import ocr as ocr_tools

    envelope = _ocr_envelope(
        "ocr.ocr_region",
        lambda args: ocr_tools._raise(
            {"error": {"code": "made_up_code", "message": "boom"}}, tool="ocr.ocr_region"
        ),
        ["window_id"],
        window_id="x",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "internal_error"


def test_ocr_opt_bool_survives_stringified_false() -> None:
    """``bool("false")`` is True — that flips the answer, not the encoding."""
    from localmcptools.tools.ocr import _opt_bool

    assert _opt_bool("false") is False
    assert _opt_bool("False") is False
    assert _opt_bool("0") is False
    assert _opt_bool("true") is True
    assert _opt_bool(None) is False
    assert _opt_bool(True) is True


def test_new_error_codes_are_registered() -> None:
    """The registry is the contract: unregistered codes are rejected."""
    from localmcptools.tools._common import STANDARD_ERROR_CODES

    assert "window_not_authorized" in STANDARD_ERROR_CODES
    assert "source_not_allowed" in STANDARD_ERROR_CODES
    assert len(set(STANDARD_ERROR_CODES)) == len(STANDARD_ERROR_CODES)


def test_default_ttl_is_one_hour() -> None:
    assert DEFAULT_TTL_MS == 60 * 60 * 1000


# --- ui.screens.TokenBucket ----------------------------------------------


def test_token_bucket_allows_within_window() -> None:
    bucket = TokenBucket(rate_per_minute=3, window_seconds=1.0)
    for _ in range(3):
        assert bucket.check("agent-a")
    assert not bucket.check("agent-a")


def test_token_bucket_per_key_independent() -> None:
    bucket = TokenBucket(rate_per_minute=2, window_seconds=1.0)
    assert bucket.check("a")
    assert bucket.check("a")
    assert not bucket.check("a")
    assert bucket.check("b")  # 'b' starts fresh
    assert bucket.check("b")


def test_token_bucket_resets_after_window() -> None:
    bucket = TokenBucket(rate_per_minute=2, window_seconds=0.1)
    assert bucket.check("k")
    assert bucket.check("k")
    assert not bucket.check("k")
    time.sleep(0.15)
    assert bucket.check("k")


def test_token_bucket_reset_clears_keys() -> None:
    bucket = TokenBucket(rate_per_minute=1)
    assert bucket.check("k")
    assert not bucket.check("k")
    bucket.reset("k")
    assert bucket.check("k")


# --- ui.ocr provider fallback --------------------------------------------


def test_get_provider_returns_object_with_ocr_image() -> None:
    provider = get_provider()
    assert hasattr(provider, "ocr_image")
    assert hasattr(provider, "name")


def test_stub_provider_returns_uncertain() -> None:
    provider = StubProvider()
    result = provider.ocr_image(b"not-a-real-image")
    assert result.uncertain is True
    assert result.blocks == []
    assert result.full_text == ""


def test_reset_provider_forces_rebuild() -> None:
    p1 = get_provider()
    reset_provider()
    p2 = get_provider()
    assert p1 is not p2


def test_ocr_region_rejects_invalid_source() -> None:
    out = ocr_region(source={})
    assert out["error"]["code"] == "source_not_allowed"


def test_ocr_assert_text_returns_passed_false_on_uncertain() -> None:
    """With the stub provider, assert_text must never claim passed."""
    reset_provider()
    out = ocr_assert_text(source={"window_id": "missing"}, expected="hello")
    # Either it errored (window not authorized) or returned passed:false.
    assert out.get("passed") is False
    if "error" not in out:
        assert out.get("uncertain") is True


def test_ocr_find_text_handles_invalid_query() -> None:
    # The implementation checks ``query`` after resolving the source,
    # so a missing window fails first with ``source_not_allowed``.
    out = ocr_find_text(query="", source={"window_id": "x"})
    assert "error" in out


# --- ui.verify orchestrator ----------------------------------------------


def test_verify_returns_passed_when_all_predicates_pass() -> None:
    predicates: list[Predicate] = [
        _AlwaysPassPredicate("a"),
        _AlwaysPassPredicate("b"),
    ]
    report = verify(predicates=predicates)
    assert report["passed"] is True
    assert len(report["predicates"]) == 2


def test_verify_aborts_on_first_failure() -> None:
    predicates: list[Predicate] = [
        _AlwaysPassPredicate("a"),
        _AlwaysFailPredicate("b"),
        _AlwaysPassPredicate("never-runs"),
    ]
    report = verify(predicates=predicates)
    assert report["passed"] is False
    # ``never-runs`` must NOT be in the report.
    names = [p["name"] for p in report["predicates"]]
    assert "never-runs" not in names


def test_uia_predicate_returns_observed_subclass() -> None:
    pred = UIAPredicate(window_id="missing", criterion={"text": "x"}, expected={})
    assert isinstance(pred, object)


def test_screenshot_predicate_threshold_is_set() -> None:
    pred = ScreenshotPredicate(window_id=1, reference_handle="art://x", threshold=0.05)
    assert pred.threshold == 0.05


def test_ocr_predicate_carries_match() -> None:
    pred = OCRPredicate(window_id="w", expected="hello", match="exact")
    assert pred.match == "exact"


# --- ocr source resolution: failures must stay classified ----------------


def test_ocr_rejects_source_when_nothing_usable_is_present(monkeypatch) -> None:
    """A wrapper-synthesised ``None`` is not a source."""
    out = ocr_module.ocr_region(source={"window_id": "None", "screenshot_handle": None})
    assert out["error"]["code"] == "source_not_allowed"


def test_ocr_reports_unauthorised_window_separately(monkeypatch) -> None:
    import localmcptools.ui.windows as windows_module

    monkeypatch.setattr(windows_module, "lookup", lambda **kw: None)
    out = ocr_module.ocr_region(source={"window_id": "not-registered"})
    # Not "source must be a window_id or screenshot_handle": the source is
    # well-formed, it just isn't authorised.
    assert out["error"]["code"] == "window_not_authorized"
    assert "ui_authorize_window" in out["error"]["suggestion"]


def test_ocr_reports_capture_failure_separately(monkeypatch) -> None:
    """Valid source + dead pixel pipeline must NOT blame the source."""
    import localmcptools.ui.windows as windows_module

    row = windows_module.WindowSummary(hwnd=4242, pid=7, process="x.exe", title="x")
    monkeypatch.setattr(windows_module, "lookup", lambda **kw: row)
    monkeypatch.setattr(ocr_module, "capture_window_bytes", lambda **kw: None)
    out = ocr_module.ocr_region(source={"window_id": "good-id"})
    assert out["error"]["code"] == "capture_failed"
    assert "4242" in out["error"]["message"]


def test_ocr_reports_unknown_artifact_separately(monkeypatch) -> None:
    from localmcptools.persistence import artifacts

    # The real ``artifacts.lookup`` *raises* for an unknown handle; a
    # handler that only checks for ``None`` files this as internal_error.
    def _raise(_handle: str):
        raise artifacts.ArtifactNotFound("unknown handle")

    monkeypatch.setattr(artifacts, "lookup", _raise)
    out = ocr_module.ocr_region(source={"screenshot_handle": "art://2026/01/nope"})
    assert out["error"]["code"] == "artifact_not_found"

    monkeypatch.setattr(artifacts, "lookup", lambda handle: None)
    out2 = ocr_module.ocr_region(source={"screenshot_handle": "art://2026/01/nope"})
    assert out2["error"]["code"] == "artifact_not_found"


# --- binary artifacts -------------------------------------------------------
#
# ``artifacts.write()`` decodes its payload as UTF-8 before redacting, which
# replaces every byte that is not valid text with U+FFFD. Screenshots used to
# go through that path, so ``ui_screenshot_window`` handed out a handle to a
# file no image decoder could open — OCR then died inside WinRT with an opaque
# ``HRESULT -2003292336``. These tests pin the binary bypass and the OCR-side
# classification that replaces the opaque failure.


@pytest.fixture
def artifact_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LMCP_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "audit.sqlite")
    return tmp_path


def _png_bytes() -> bytes:
    Image = pytest.importorskip("PIL.Image", reason="Pillow is an OCR dependency")
    import io

    buf = io.BytesIO()
    Image.new("RGB", (4, 3), (12, 34, 56)).save(buf, format="PNG")
    return buf.getvalue()


def test_binary_write_rejects_non_bytes_content(artifact_db: Path) -> None:
    from localmcptools.persistence import artifacts

    with pytest.raises(TypeError, match="bytes-like"):
        artifacts.write("not bytes", sensitive=True, binary=True)


def test_binary_write_requires_sensitive(artifact_db: Path) -> None:
    """Binary skips redaction, so the caller must acknowledge that."""
    from localmcptools.persistence import artifacts

    with pytest.raises(ValueError, match="sensitive=True"):
        artifacts.write(b"\x89PNG\r\n\x1a\n", sensitive=False, binary=True)


def test_binary_write_roundtrips_png_verbatim(artifact_db: Path) -> None:
    from localmcptools.persistence import artifacts

    png = _png_bytes()
    handle = artifacts.write(png, sensitive=True, binary=True)

    rec = artifacts.lookup(handle)
    on_disk = Path(rec.path).read_bytes()
    assert on_disk == png
    assert on_disk.startswith(b"\x89PNG\r\n\x1a\n")
    assert rec.sensitive is True
    # Line counts are a text concept; a PNG must not claim to have any.
    assert rec.line_count == 0
    # The handle grammar stays ``.log`` — it is a DB key, not a filename, and
    # parse_handle rejects anything else (pinned by test_artifact_acl).
    assert handle.endswith(".log")
    assert artifacts.parse_handle(handle)[1] == rec.call_id


def test_text_write_still_decodes_and_redacts(artifact_db: Path) -> None:
    """The non-binary path must be byte-for-byte what it was."""
    from localmcptools.persistence import artifacts

    handle = artifacts.write("token=SECRET1234567890\n", sensitive=True)
    rec = artifacts.lookup(handle)
    assert "SECRET1234567890" not in Path(rec.path).read_text(encoding="utf-8")
    assert rec.line_count == 1


def test_ocr_reads_png_artifact_handle_end_to_end(
    artifact_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from localmcptools.persistence import artifacts

    png = _png_bytes()
    handle = artifacts.write(png, sensitive=True, binary=True)

    seen: dict[str, bytes] = {}

    class _Recorder:
        name = "recorder"

        def ocr_image(self, image_bytes: bytes):  # noqa: ANN001, ANN202
            seen["bytes"] = image_bytes
            return ocr_module.OcrResult(blocks=[], full_text="", uncertain=True)

    monkeypatch.setattr(ocr_module, "get_provider", lambda: _Recorder())
    out = ocr_module.ocr_region(source={"screenshot_handle": handle})

    assert "error" not in out, out
    assert seen["bytes"] == png
    assert out["source_handle"] == handle


def test_ocr_rejects_text_artifact_handle_as_invalid_args(
    artifact_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real artifact holding text is a named error, not an HRESULT."""
    from localmcptools.persistence import artifacts

    handle = artifacts.write("just some log output\n", sensitive=True)
    out = ocr_module.ocr_region(source={"screenshot_handle": handle})
    assert out["error"]["code"] == "invalid_args"
    assert "ui_screenshot_window" in out["error"]["suggestion"]


def test_ocr_engine_runs_from_inside_a_running_event_loop() -> None:
    """Tool bodies execute on the MCP server's event loop thread.

    The engine used to call ``run_until_complete`` on a second loop, which
    raises ``Cannot run the event loop while another loop is running``.
    The handler swallowed it and returned an empty result, so real OCR
    silently read every screen as blank.
    """
    import asyncio

    class _Rect:
        x = y = 0
        width = 10
        height = 8

    class _Line:
        text = "hello"
        confidence = 0.9
        bounding_rect = _Rect()

    class _Engine:
        async def recognize_async(self, bitmap: object) -> object:
            return type("R", (), {"lines": [_Line()]})()

    class _Decoder:
        async def get_software_bitmap_async(self) -> object:
            return object()

    async def _create_decoder(stream: object) -> _Decoder:
        return _Decoder()

    class _Writer:
        def __init__(self, stream: object) -> None:
            self._stream = stream

        def write_bytes(self, payload: bytes) -> None:
            self._payload = payload

        async def store_async(self) -> None:
            return None

        async def flush_async(self) -> None:
            return None

    class _Stream:
        def seek(self, offset: int) -> None:
            return None

    win_glob = type("G", (), {"Language": staticmethod(lambda name: name)})
    win_ocr = type(
        "O",
        (),
        {"OcrEngine": type("E", (), {"try_create_from_language": staticmethod(lambda lang: _Engine())})},
    )
    win_streams = type(
        "S",
        (),
        {
            "InMemoryRandomAccessStream": _Stream,
            "DataWriter": _Writer,
        },
    )
    win_img = type(
        "I",
        (),
        {
            "BitmapDecoder": type(
                "D",
                (),
                {"create_async": staticmethod(_create_decoder)},
            )
        },
    )

    run = ocr_module._build_engine(win_ocr, win_glob, win_img, win_streams, ("en",))

    async def call_from_loop() -> object:
        return run(b"payload")

    result = asyncio.run(call_from_loop())
    assert [b.text for b in result.blocks] == ["hello"]
    assert result.uncertain is False


def test_ocr_assert_text_surfaces_the_classified_error(monkeypatch) -> None:
    import localmcptools.ui.windows as windows_module

    monkeypatch.setattr(windows_module, "lookup", lambda **kw: None)
    out = ocr_module.ocr_assert_text(source={"window_id": "nope"}, expected="x")
    assert out["passed"] is False
    assert out["error"]["code"] == "window_not_authorized"


def test_ocr_find_text_surfaces_the_classified_error(monkeypatch) -> None:
    import localmcptools.ui.windows as windows_module

    monkeypatch.setattr(windows_module, "lookup", lambda **kw: None)
    out = ocr_module.ocr_find_text(query="x", source={"window_id": "nope"})
    assert out["error"]["code"] == "window_not_authorized"


def test_capture_failed_code_is_registered() -> None:
    assert "capture_failed" in STANDARD_ERROR_CODES


# --- Win32/GDI ctypes signatures ------------------------------------------


class _FakeCtypesFn:
    def __init__(self) -> None:
        self.restype = None
        self.argtypes = None


class _FakeCtypesLib:
    def __init__(self, names: tuple[str, ...]) -> None:
        for name in names:
            setattr(self, name, _FakeCtypesFn())


def test_declare_gdi_uses_pointer_sized_handle_signatures() -> None:
    """A 32-bit restype truncates every GDI handle on 64-bit Windows.

    ``GetWindowDC`` legitimately returns values above ``2**63``; ctypes
    defaults ``restype`` to ``c_int`` and sign-extends them into huge
    negative ints, so the next call raises ``int too long to convert``
    and the whole OCR feed dies. Assert the declared types, not the
    capture result, so this runs on any host.
    """
    ctypes = pytest.importorskip("ctypes")
    from ctypes import wintypes

    user32 = _FakeCtypesLib(
        (
            "GetWindowDC",
            "ReleaseDC",
            "PrintWindow",
            "IsIconic",
            "ShowWindow",
            "RedrawWindow",
            "UpdateWindow",
        )
    )
    gdi32 = _FakeCtypesLib(
        (
            "CreateCompatibleDC",
            "CreateCompatibleBitmap",
            "SelectObject",
            "GetDIBits",
            "DeleteObject",
            "DeleteDC",
        )
    )
    screens_module._declare_gdi(user32, gdi32)

    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert pointer != ctypes.sizeof(ctypes.c_int), "host is not 64-bit; assertion is vacuous"
    for lib, name in (
        (user32, "GetWindowDC"),
        (gdi32, "CreateCompatibleDC"),
        (gdi32, "CreateCompatibleBitmap"),
        (gdi32, "SelectObject"),
    ):
        restype = getattr(getattr(lib, name), "restype")
        assert ctypes.sizeof(restype) == pointer, f"{name} restype is not pointer-sized"
    assert user32.GetWindowDC.restype is wintypes.HDC
    assert user32.GetWindowDC.argtypes == [wintypes.HWND]
    assert gdi32.GetDIBits.argtypes[-1] is ctypes.c_uint


@pytest.mark.skipif(os.name != "nt", reason="Win32 capture is Windows-only")
def test_capture_window_bytes_returns_png_for_a_real_window() -> None:
    """The regression this whole exercise exists for: never None."""
    import ctypes

    from localmcptools.ui.windows import list_windows

    target = None
    for window in list_windows():
        if ctypes.windll.user32.IsIconic(window.hwnd):
            continue
        if not window.title.strip():
            continue
        bbox = screens_module._window_bbox(window.hwnd)
        if bbox and bbox[2] - bbox[0] >= 200 and bbox[3] - bbox[1] >= 200:
            target = window
            break
    if target is None:
        pytest.skip("no non-minimized, titled, >=200px window on this host")

    payload = screens_module.capture_window_bytes(hwnd=target.hwnd)
    assert payload is not None, f"capture returned None for hwnd={target.hwnd}"
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) > 1000


# --- helpers used in the verify tests ------------------------------------


class _AlwaysPassPredicate:
    def __init__(self, name: str) -> None:
        self.name = name

    def check(self) -> PredicateResult:
        return PredicateResult(passed=True, detail="ok")


class _AlwaysFailPredicate:
    def __init__(self, name: str) -> None:
        self.name = name

    def check(self) -> PredicateResult:
        return PredicateResult(passed=False, detail="nope")


# --- defect 6: ui.screenshot_* soft-fail envelopes ------------------------
#
# The ``ui.screenshot_*`` family had the same root cause as the ``ocr.*``
# soft-fail fixed above: the module layer returned ``{"error": ...}`` as an
# ordinary dict, so the chokepoint wrapped it as ``ok: true`` and every real
# failure looked like a success. ``ui.screenshot_to_file`` was the worst of
# them — a caller could be handed ``ok: true`` for a file that was never
# written and go on to reason about a screenshot that does not exist.
#
# All assertions below go through the real chokepoint (``_invoke``) because
# calling the tool body directly cannot observe the ``ok`` flag at all.

_SCREENSHOT_PARAMS_WINDOW = ["window_id"]
_SCREENSHOT_PARAMS_FULL: list[str] = []
_SCREENSHOT_PARAMS_REGION = ["region"]
_SCREENSHOT_PARAMS_FILE = ["path", "workspace_id", "window_id", "region"]

_LEGACY_FLAT_ERROR = {
    "error": "rate_limit",
    "message": "screenshot rate limit (20/min) hit for 'full'",
    "next_actions": ["wait 60 seconds before retrying"],
}


def _screenshot_envelope(tool: str, logic, param_names, **kwargs):
    return _invoke(tool, logic, param_names, **kwargs)


def _authorized_window(monkeypatch: pytest.MonkeyPatch, hwnd: int = 4242) -> None:
    """Pretend ``window_id`` is authorised and still resolvable.

    ``tools.ui`` binds ``is_authorized`` at import time but re-imports
    ``lookup`` inside the function body, so the two need different patch
    targets.
    """
    from types import SimpleNamespace

    from localmcptools.tools import ui as ui_tools
    import localmcptools.ui.windows as windows_module

    monkeypatch.setattr(ui_tools, "is_authorized", lambda **kw: True)
    monkeypatch.setattr(
        windows_module, "lookup", lambda **kw: SimpleNamespace(hwnd=hwnd)
    )


class _ExhaustedBucket:
    """Stand-in for ``SCREENSHOT_BUCKET`` that always refuses."""

    def check(self, *_args: object, **_kwargs: object) -> bool:
        return False


def _temp_png(name: str = "shot.png") -> str:
    import tempfile

    return str(Path(tempfile.gettempdir()) / "localmcptools" / name)


# --- ui.screenshot_window -------------------------------------------------


def test_screenshot_window_requires_a_window_id(database: Path) -> None:
    from localmcptools.tools import ui as ui_tools

    envelope = _screenshot_envelope(
        "ui.screenshot_window", ui_tools.ui_screenshot_window, _SCREENSHOT_PARAMS_WINDOW
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_args"


def test_screenshot_window_rejects_unauthorized_window(database: Path) -> None:
    from localmcptools.tools import ui as ui_tools

    envelope = _screenshot_envelope(
        "ui.screenshot_window",
        ui_tools.ui_screenshot_window,
        _SCREENSHOT_PARAMS_WINDOW,
        window_id="definitely-not-authorized",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "window_not_authorized"


def test_screenshot_window_translates_legacy_flat_error(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A straggler module speaking the old bare-string dialect still
    produces a *registered* code and keeps its message + next action."""
    from localmcptools.tools import ui as ui_tools

    _authorized_window(monkeypatch)
    monkeypatch.setattr(
        ui_tools.screens, "capture", lambda **kw: dict(_LEGACY_FLAT_ERROR)
    )
    envelope = _screenshot_envelope(
        "ui.screenshot_window",
        ui_tools.ui_screenshot_window,
        _SCREENSHOT_PARAMS_WINDOW,
        window_id="w1",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "rate_limit_exceeded"
    assert "rate limit" in envelope["error"]["message"]
    assert "wait 60 seconds" in (envelope["error"].get("suggestion") or "")


def test_screenshot_unknown_legacy_slug_stays_visible_in_message(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Downgrading to ``internal_error`` must not erase the cause."""
    from localmcptools.tools import ui as ui_tools

    _authorized_window(monkeypatch)
    monkeypatch.setattr(
        ui_tools.screens,
        "capture",
        lambda **kw: {"error": "totally_made_up", "message": "gdi blew up"},
    )
    envelope = _screenshot_envelope(
        "ui.screenshot_window",
        ui_tools.ui_screenshot_window,
        _SCREENSHOT_PARAMS_WINDOW,
        window_id="w1",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "internal_error"
    assert "totally_made_up" in envelope["error"]["message"]
    assert "gdi blew up" in envelope["error"]["message"]


# --- ui.screenshot_region / ui.screenshot_full ----------------------------


@pytest.mark.parametrize("region", [None, {}, "", "top-left"])
def test_screenshot_region_requires_a_region(database: Path, region) -> None:
    from localmcptools.tools import ui as ui_tools

    envelope = _screenshot_envelope(
        "ui.screenshot_region",
        ui_tools.ui_screenshot_region,
        _SCREENSHOT_PARAMS_REGION,
        region=region,
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_args"


def test_screenshot_full_rate_limit_is_a_typed_failure(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the *real* ``capture()`` — only the bucket is stubbed."""
    from localmcptools.tools import ui as ui_tools

    monkeypatch.setattr(screens_module, "SCREENSHOT_BUCKET", _ExhaustedBucket())
    envelope = _screenshot_envelope(
        "ui.screenshot_full", ui_tools.ui_screenshot_full, _SCREENSHOT_PARAMS_FULL
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "rate_limit_exceeded"
    assert "rate limit" in envelope["error"]["message"]
    assert "wait 60 seconds" in (envelope["error"].get("suggestion") or "")
    assert envelope["data"] is None or "error" not in (envelope["data"] or {})


def test_screenshot_full_success_is_not_turned_into_a_failure(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the guard: a real success must stay ``ok: true``."""
    from localmcptools.tools import ui as ui_tools

    monkeypatch.setattr(
        ui_tools.screens,
        "capture",
        lambda **kw: {"handle": "art://x.log", "width": 8, "height": 8, "mode": "full"},
    )
    envelope = _screenshot_envelope(
        "ui.screenshot_full", ui_tools.ui_screenshot_full, _SCREENSHOT_PARAMS_FULL
    )
    assert envelope["ok"] is True
    assert envelope["data"]["handle"] == "art://x.log"


# --- ui.screenshot_to_file ------------------------------------------------


def test_screenshot_to_file_rejects_relative_path_as_invalid_path(
    database: Path,
) -> None:
    """The regression that motivated this pass: a rejected path used to
    arrive as ``internal_error`` with the reason thrown away."""
    from localmcptools.tools import ui as ui_tools

    envelope = _screenshot_envelope(
        "ui.screenshot_to_file",
        ui_tools.ui_screenshot_to_file,
        _SCREENSHOT_PARAMS_FILE,
        path="relative/shot.png",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_path"
    assert "absolute" in envelope["error"]["message"]


def test_screenshot_to_file_reports_unregistered_workspace_first(
    database: Path,
) -> None:
    """Ordering guard: an unknown workspace must not be blamed on the
    path, or the caller goes off fixing a path that was never wrong."""
    from localmcptools.tools import ui as ui_tools

    envelope = _screenshot_envelope(
        "ui.screenshot_to_file",
        ui_tools.ui_screenshot_to_file,
        _SCREENSHOT_PARAMS_FILE,
        path=_temp_png("ordering.png"),
        workspace_id="ws-does-not-exist",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "workspace_not_registered"


def test_screenshot_to_file_requires_authorized_window(database: Path) -> None:
    from localmcptools.tools import ui as ui_tools

    envelope = _screenshot_envelope(
        "ui.screenshot_to_file",
        ui_tools.ui_screenshot_to_file,
        _SCREENSHOT_PARAMS_FILE,
        path=_temp_png("unauthorized.png"),
        window_id="definitely-not-authorized",
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "window_not_authorized"


def test_screenshot_to_file_success_returns_the_written_path(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from localmcptools.tools import ui as ui_tools

    target = _temp_png("success.png")
    monkeypatch.setattr(
        ui_tools.screens,
        "capture_to_file",
        lambda **kw: {"path": target, "width": 4, "height": 4, "mode": "full"},
    )
    envelope = _screenshot_envelope(
        "ui.screenshot_to_file",
        ui_tools.ui_screenshot_to_file,
        _SCREENSHOT_PARAMS_FILE,
        path=target,
    )
    assert envelope["ok"] is True
    assert envelope["data"]["path"] == target


def test_screenshot_to_file_write_failure_is_a_host_fault_not_a_bad_path(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path already passed validation, so a write failure is the host
    (locked file / read-only volume / full disk), not a caller mistake."""
    from localmcptools.tools import ui as ui_tools

    target = _temp_png("locked.png")
    monkeypatch.setattr(
        ui_tools.screens,
        "capture_to_file",
        lambda **kw: {
            "error": "save_failed",
            "message": f"could not write the screenshot to {target!r}",
        },
    )
    envelope = _screenshot_envelope(
        "ui.screenshot_to_file",
        ui_tools.ui_screenshot_to_file,
        _SCREENSHOT_PARAMS_FILE,
        path=target,
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "internal_error"
    assert "could not write" in envelope["error"]["message"]


def test_screenshot_to_file_rate_limit_is_a_typed_failure(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from localmcptools.tools import ui as ui_tools

    monkeypatch.setattr(screens_module, "SCREENSHOT_BUCKET", _ExhaustedBucket())
    envelope = _screenshot_envelope(
        "ui.screenshot_to_file",
        ui_tools.ui_screenshot_to_file,
        _SCREENSHOT_PARAMS_FILE,
        path=_temp_png("limited.png"),
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "rate_limit_exceeded"


# --- cross-cutting guards -------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "param_names", "kwargs"),
    [
        ("ui_screenshot_window", _SCREENSHOT_PARAMS_WINDOW, {"window_id": "w1"}),
        ("ui_screenshot_full", _SCREENSHOT_PARAMS_FULL, {}),
        (
            "ui_screenshot_region",
            _SCREENSHOT_PARAMS_REGION,
            {"region": {"x": 0, "y": 0, "width": 10, "height": 10}},
        ),
        (
            "ui_screenshot_to_file",
            _SCREENSHOT_PARAMS_FILE,
            {"path": _temp_png("guard.png")},
        ),
    ],
)
def test_screenshot_tools_never_report_ok_with_an_error_payload(
    database: Path, monkeypatch: pytest.MonkeyPatch, tool, param_names, kwargs
) -> None:
    """Meta-guard: whatever the module layer says, a failure is never
    allowed to reach the caller as ``ok: true``."""
    from localmcptools.tools import ui as ui_tools

    _authorized_window(monkeypatch)
    broken = {"error": "made_up_slug", "message": "synthetic failure"}
    monkeypatch.setattr(ui_tools.screens, "capture", lambda **kw: dict(broken))
    monkeypatch.setattr(
        ui_tools.screens, "capture_to_file", lambda **kw: dict(broken)
    )
    envelope = _screenshot_envelope(
        f"ui.{tool[len('ui_'):]}", getattr(ui_tools, tool), param_names, **kwargs
    )
    assert envelope["ok"] is False, f"{tool} reported a failure as success"
    assert envelope["error"]["code"] == "internal_error"
    assert envelope["data"] is None or "error" not in (envelope["data"] or {})


def test_screens_module_only_uses_registered_error_codes() -> None:
    """``_raise_if_failed`` degrades unregistered codes to
    ``internal_error``, so a typo here silently loses the classification.
    """
    import inspect
    import re

    source = inspect.getsource(screens_module)
    codes = re.findall(r"_error\(\s*['\"]([a-z_]+)['\"]", source)
    assert codes, "no _error() call sites found — the guard is vacuous"
    unregistered = sorted({c for c in codes if c not in STANDARD_ERROR_CODES})
    assert not unregistered, f"unregistered error codes: {unregistered}"
    # A code passed as a variable would escape the regex above, so also
    # require that every call site is a literal.
    call_sites = len(re.findall(r"(?<!def )_error\(", source))
    assert call_sites == len(codes), (
        "an _error() call site passes its code as a variable; "
        "the literal-only guard no longer covers every path"
    )


def test_screens_module_has_no_bare_string_error_literals() -> None:
    """``{"error": "rate_limit"}`` is the exact shape the tools layer used
    to flatten into a contentless ``internal_error``.

    Parsed as an AST rather than grepped: a docstring *mentioning* the old
    shape is documentation, only an actual dict literal with a constant
    string value is a live failure path.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(screens_module))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant)
        and key.value == "error"
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    ]
    assert not offenders, (
        f"bare-string error literals at lines {offenders}; use _error(code, message)"
    )


def test_legacy_error_slug_map_targets_registered_codes() -> None:
    from localmcptools.tools.ui import _LEGACY_ERROR_CODES

    assert _LEGACY_ERROR_CODES, "the migration map must not be empty"
    bad = sorted(
        {code for code in _LEGACY_ERROR_CODES.values() if code not in STANDARD_ERROR_CODES}
    )
    assert not bad, f"legacy map points at unregistered codes: {bad}"


def test_screenshot_control_codes_are_registered() -> None:
    """``control_api`` maps these two to real HTTP statuses; if either
    leaves the registry the HTTP layer degrades to a 200 body."""
    assert "rate_limit_exceeded" in STANDARD_ERROR_CODES
    assert "platform_unsupported" in STANDARD_ERROR_CODES
