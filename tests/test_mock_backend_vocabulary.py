"""The dev-mode mock backend must not speak a different protocol than the real one.

`frontend/e2e/*` drives the app against `frontend/src/preview.tsx`, which implements
the NDJSON protocol in the browser: the specs are the only layer that exercises the
real renderer, and they are also the only layer that can pass while a renamed or
removed RPC still breaks the shipped app. These assertions bind the mock's vocabulary
to the backend's actual method table and to the events the backend emits.

Payload *shapes* are deliberately out of scope — a hand-written mock cannot be
structurally diffed against numpy-backed responses. Names and events are the drift
that has actually happened in this project's history, and the cheap thing to keep true.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from mdescriptor_studio_backend.analysis import ANALYSIS_REGISTRY
from mdescriptor_studio_backend.main import build_methods
from mdescriptor_studio_backend.protocol import frames
from mdescriptor_studio_backend.services.analysis_helpers import (
    ANALYSIS_ALGORITHM_VERSION,
    FEATURE_CORRELATION_SCHEMA,
    FEATURE_VARIANCE_SCHEMA,
)

ROOT = Path(__file__).resolve().parents[1]

# Triple-quoted because the character class has to contain both quote styles.
REQUEST_RE = r'''ipc\.request(?:<[^>]*>)?\(\s*["']([a-z_]+(?:\.[a-z_]+)+)["']'''
ON_RE = r'''ipc\.on\(\s*["']([a-zA-Z_.]+)["']'''
# `(?:\.[a-z_]+)+`, not a single optional group: five of the mock's handlers are
# `dataset.view.*`, and a one-dot pattern left them out of mock_methods() in both
# directions - renaming or inventing one there passed this file silently, and the
# vacuity guard below was already satisfied without them.
ROUTE_RE = r'^\s{2}"([a-z_]+(?:\.[a-z_]+)+)":'  # preview.tsx's request table keys
MOCK_EMIT_RE = r'mockEmit\(\s*"([a-zA-Z_.]+)"'
EMIT_RE = r'''\.emit\(\s*["']([a-zA-Z_.]+)["']'''

FRONTEND_SRC = ROOT / "frontend" / "src"


class _Any:
    """Stands in for a service: build_methods only stores and getattr()s."""

    def __getattr__(self, name: str):
        return lambda *args, **kwargs: None


def backend_methods() -> set[str]:
    return set(
        build_methods(
            _Any(), _Any(), _Any(), _Any(), _Any(), _Any(), _Any(), _Any(), {}, ROOT / "data",
        )
    )


def _literals(path: Path, pattern: str, *, multiline: bool = False) -> set[str]:
    flags = re.M if multiline else 0
    return set(re.findall(pattern, path.read_text(encoding="utf-8"), flags))


def mock_methods() -> set[str]:
    """The keys of preview.tsx's request table — not every dotted string in it.

    Matching the table's shape keeps job types and event names (which appear as
    *values* there) out of a comparison that is about RPC vocabulary.
    """
    return _literals(FRONTEND_SRC / "preview.tsx", ROUTE_RE, multiline=True)


def mock_events() -> set[str]:
    return _literals(FRONTEND_SRC / "preview.tsx", MOCK_EMIT_RE) | {"backend.ready"}


def frontend_called_methods() -> set[str]:
    names: set[str] = set()
    for source in sorted(FRONTEND_SRC.rglob("*.ts*")):
        if ".test." in source.name or source.name == "preview.tsx":
            continue
        names |= _literals(source, REQUEST_RE)
    return names


def backend_events() -> set[str]:
    names: set[str] = set()
    for source in sorted((ROOT / "backend" / "mdescriptor_studio_backend").rglob("*.py")):
        names |= _literals(source, EMIT_RE)
    return names


def frontend_listened_events() -> set[str]:
    names: set[str] = set()
    for source in sorted(FRONTEND_SRC.rglob("*.ts*")):
        if ".test." in source.name or source.name == "preview.tsx":
            continue
        names |= _literals(source, ON_RE)
    return names


def test_the_extraction_itself_is_not_vacuous():
    # A regex that silently matched nothing would turn every assertion below
    # into a pass.
    methods = backend_methods()
    assert {"system.info", "dataset.list", "descriptor.submit", "job.cancel"} <= methods
    assert len(methods) >= 30, len(methods)
    assert len(mock_methods()) >= 25, mock_methods()
    assert len(frontend_called_methods()) >= 15, frontend_called_methods()
    assert {"backend.ready", "job.progress", "job.finished"} <= backend_events()
    assert {"backend.ready", "job.progress", "job.finished"} <= frontend_listened_events()
    assert {"system.info", "dataset.list", "analysis.pca"} <= mock_methods(), mock_methods()
    assert "job.progress" in mock_events(), mock_events()
    # And specifically for ROUTE_RE: five handlers live under `dataset.view.*`,
    # which a pattern allowing one dot only could not see in either direction.
    views = {name for name in mock_methods() if name.count(".") > 1}
    assert {"dataset.view.create", "dataset.view.list", "dataset.view.remove",
            "dataset.view.rename", "dataset.view.split"} <= views, views


def test_registry_names_are_callable_rpc_names():
    # main.build_methods derives `analysis.<name>` straight from the registry, so
    # a registered algorithm with no matching AnalysisService method is a wiring
    # bug that must fail here rather than at first request.
    missing = [name for name in ANALYSIS_REGISTRY.names() if f"analysis.{name}" not in backend_methods()]
    assert missing == []


def test_the_mock_only_answers_real_backend_methods():
    invented = sorted(mock_methods() - backend_methods())
    assert not invented, (
        "preview.tsx answers methods the backend does not register; the e2e suite "
        "would pass against an API that no longer exists: " + ", ".join(invented)
    )


def test_the_mock_only_emits_events_the_backend_emits():
    orphan = sorted(mock_events() - backend_events())
    assert not orphan, "preview.tsx emits events the backend never sends: " + ", ".join(orphan)


def test_the_frontend_only_calls_registered_methods():
    unknown = sorted(frontend_called_methods() - backend_methods())
    assert not unknown, "renderer calls unregistered RPC methods: " + ", ".join(unknown)


def test_the_renderer_only_listens_for_events_the_backend_emits():
    orphan = sorted(frontend_listened_events() - backend_events())
    assert not orphan, "renderer subscribes to events nothing emits: " + ", ".join(orphan)


def test_the_mock_speaks_the_current_protocol_version():
    # preview.tsx stubs window.__TAURI_INTERNALS__, so the client's version check
    # runs against numbers the mock invents; a stale mock makes every e2e run a
    # test of code the renderer would reject.
    literals = _literals(
        FRONTEND_SRC / "preview.tsx",
        r"protocol_version:\s*(\d+)",
    )
    assert literals, "no protocol_version literal found in preview.tsx"
    assert set(literals) == {str(frames.PROTOCOL_VERSION)}, literals


def test_the_mock_copies_the_current_analysis_schema_revisions():
    # preview.tsx stamps these into its canned feature previews and into the
    # analysis rows it records. They are part of the cache identity, so a mock
    # that keeps an old number restores history under semantics the app would
    # treat as a different analysis - and every e2e spec stays green.
    text = (FRONTEND_SRC / "preview.tsx").read_text(encoding="utf-8")
    for name, revision in (
        ("feature_variance_schema", FEATURE_VARIANCE_SCHEMA),
        ("feature_correlation_schema", FEATURE_CORRELATION_SCHEMA),
    ):
        literals = set(re.findall(rf"{name}:\s*(\d+)", text))
        assert literals == {str(revision)}, f"preview.tsx {name}: {literals} != backend {revision}"
    # The canned previews carry the same revision under their own field name.
    for kind, revision in (
        ("feature_variance", FEATURE_VARIANCE_SCHEMA),
        ("feature_correlation", FEATURE_CORRELATION_SCHEMA),
    ):
        literals = set(re.findall(rf'kind: "{kind}",\s*schema_version: (\d+)', text))
        assert literals == {str(revision)}, f"preview.tsx {kind} preview schema: {literals}"


def test_the_mock_reports_the_current_analysis_algorithm_version():
    # The same argument for the number that decides whether a stored analysis
    # result is still valid: the UI shows it, and an e2e run against a mock that
    # still names the previous revision tests a cache the app would reject.
    literals = _literals(
        FRONTEND_SRC / "preview.tsx",
        r'analysis_algorithm_version:\s*"([^"]+)"',
    )
    assert literals, "no analysis_algorithm_version literal found in preview.tsx"
    assert literals == {ANALYSIS_ALGORITHM_VERSION}, literals


def _mock_string_list(name: str) -> set[str]:
    """The members of one `const NAME = ["a", "b"]` table in preview.tsx."""
    text = (FRONTEND_SRC / "preview.tsx").read_text(encoding="utf-8")
    match = re.search(rf"const {name} = \[(.*?)\];", text, re.S)
    assert match, f"preview.tsx no longer declares {name} - the mock stopped validating"
    members = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert members, f"{name} extracted nothing"
    return members


def test_the_mock_validates_the_same_setting_keys_as_the_sidecar():
    from mdescriptor_studio_backend.main import _ALLOWED_SETTINGS

    assert _mock_string_list("SETTING_KEYS") == set(_ALLOWED_SETTINGS)


def test_the_mock_answers_the_same_health_checks_as_the_sidecar():
    # DatasetService.findings rejects an unknown check with INVALID_PARAMS. A
    # mock that accepts anything hides the typo instead of failing on it, so the
    # two lists have to be the same list.
    text = (ROOT / "backend" / "mdescriptor_studio_backend" / "services" / "dataset_service.py").read_text(encoding="utf-8")
    match = re.search(r"known = \{(.*?)\}", text, re.S)
    assert match, "dataset_service.findings no longer spells out its check set"
    known = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert known, "the findings check set extracted nothing"
    assert _mock_string_list("FINDINGS_CHECKS") == known


def test_the_mock_only_refuses_with_codes_the_backend_can_send():
    from mdescriptor_studio_backend import errors

    sendable = {value for name, value in vars(errors).items() if name.isupper() and isinstance(value, str)}
    used = _literals(FRONTEND_SRC / "preview.tsx", r'MockError\(\s*"([A-Z_]+)"')
    assert used, "the mock throws no MockError, so nothing in the renderer has an error to branch on"
    invented = sorted(used - sendable - {"NO_HANDLER"})
    assert not invented, "preview.tsx refuses requests with codes the sidecar never sends: " + ", ".join(invented)


def test_the_mock_does_not_advertise_a_capability_the_backend_fixes_off():
    # The optimistic direction of wrong evidence: statistics.py reports one
    # capability as a literal - no supported format carries per-atom energies, so
    # `"per_atom": False` under energy - while `Overview.tsx` renders that flag as
    # a ✓ column. The mock said `true`, so every browser run showed a capability the
    # shipped app can only leave empty. The shape carve-out in this file's header
    # does not reach it: this is one literal, not a structural diff.
    source = (ROOT / "backend" / "mdescriptor_studio_backend" / "datasets" / "statistics.py").read_text(encoding="utf-8")
    block = re.search(r'"properties": \{(.*?)\n        \},', source, re.S)
    assert block, "statistics.py no longer spells its properties block in one place"
    fixed_off = {
        f"{name}.{flag}"
        for name, body in re.findall(r'"(\w+)": \{([^{}]*)\}', block.group(1))
        for flag, value in re.findall(r'"(\w+)": (\w+)', body)
        if value == "False"
    }
    assert fixed_off == {"energy.per_atom"}, fixed_off

    claimed = {
        f"{name}.{flag}"
        for name, body in re.findall(r"(\w+): \{([^{}]*)\}", (FRONTEND_SRC / "preview.tsx").read_text(encoding="utf-8"))
        for flag in re.findall(r"(\w+): true", body)
    }
    advertised = sorted(claimed & fixed_off)
    assert not advertised, f"preview.tsx advertises capabilities the sidecar fixes off: {advertised}"


# What the mock answers without a job, so no submission rules apply to them.
READ_ONLY_ANALYSIS_METHODS = {"analysis.list", "analysis.preview", "analysis.chunk", "analysis.get", "analysis.delete"}


def test_every_mock_analysis_submission_has_parameter_rules():
    # Handlers return canned results whatever they are sent, so an analysis the
    # mock answers but never checks is a module whose whole parameter surface
    # the e2e suite silently blesses - the failure mode this file exists to
    # close. Enumerating the routes rather than the rules keeps a new analysis
    # from arriving unvalidated.
    text = (FRONTEND_SRC / "preview.tsx").read_text(encoding="utf-8")
    table = re.search(r"const ANALYSIS_RUN_PARAMS: Record<string, string\[\]> = \{(.*?)\n\};", text, re.S)
    assert table, "preview.tsx no longer declares ANALYSIS_RUN_PARAMS"
    ruled = set(re.findall(r'"(analysis\.[a-z_]+)":', table.group(1)))
    assert ruled, "the submission table extracted nothing"
    routes = {name for name in mock_methods() if name.startswith("analysis.")}
    assert routes - READ_ONLY_ANALYSIS_METHODS <= ruled, sorted(routes - READ_ONLY_ANALYSIS_METHODS - ruled)
    phantom = sorted(ruled - routes)
    assert not phantom, "parameter rules name methods the mock does not answer: " + ", ".join(phantom)
