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
from mdescriptor_studio_backend.datasets.statistics import STATS_VERSION
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
    # The canned previews used to repeat the same revision a second time under
    # `schema_version`, which nothing read and which was not the number that
    # gates the cache (pass 5, 5-D6 deleted it on both sides). Assert its absence,
    # so the copy cannot creep back while the params above stay the real one.
    for kind in ("feature_variance", "feature_correlation"):
        assert not re.search(rf'kind: "{kind}",\s*schema_version:', text), (
            f"preview.tsx publishes a display schema_version for {kind} again"
        )


def test_the_mock_reports_the_current_statistics_revision():
    # `protocol.ts` declares stats_version required and the backend refuses a cache
    # whose value differs, yet the mock's canned stats payloads omitted the key
    # altogether - so no browser run ever exercised the branch that reads it, and
    # the loose `Record<string, unknown>` typing let the omission stand.
    literals = set(re.findall(r"stats_version:\s*(\d+)", (FRONTEND_SRC / "preview.tsx").read_text(encoding="utf-8")))
    assert literals == {str(STATS_VERSION)}, literals


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



def test_the_protocol_document_lists_every_declared_error_code_once() -> None:
    """S6 of `docs/plan/02-IPC_PROTOCOL.md` is where a reader learns which codes
    exist. It said "24", listed 23, and omitted the two the frontend behaviour
    actually depends on - `BUSY` back-off and `DATASET_BUSY` refusing a removal
    with live jobs (deep review pass 5, 5-D2)."""
    document = (Path(__file__).resolve().parent.parent / "docs" / "plan" / "02-IPC_PROTOCOL.md").read_text(encoding="utf-8")
    section = re.search("## 6\\..*?（(\\d+).*?```text\\n(.*?)\\n```", document, re.S)
    assert section, "section 6 of the protocol document no longer holds a code block"
    listed = section.group(2).split()
    assert len(listed) == len(set(listed)), "a code is listed twice"
    declared = re.findall(r"^([A-Z][A-Z0-9_]+) = ", (Path(__file__).resolve().parent.parent / "backend" / "mdescriptor_studio_backend" / "errors.py").read_text(encoding="utf-8"), re.M)
    assert set(listed) == set(declared), {
        "missing_from_document": sorted(set(declared) - set(listed)),
        "documented_but_undeclared": sorted(set(listed) - set(declared)),
    }
    assert int(section.group(1)) == len(listed) == len(declared), (
        f"the heading counts {section.group(1)}, the block lists {len(listed)}, errors.py declares {len(declared)}"
    )
# The renderer calls these and the browser preview has no answer, so it returns
# `NO_HANDLER` - a code the sidecar never sends. Each one is a decision made here
# rather than an accident discovered later, and the gate below fails on a new
# unmocked method so the list cannot silently grow (deep review pass 4, Q5: seven
# were unhandled, `descriptor.submit` and `dataset.remove` now are implemented
# because a browser run could not otherwise produce a descriptor run or reach a
# refusal).
UNMOCKED_METHODS = {
    # Cancelling a preview job that finishes on a timer exercises the renderer's
    # bookkeeping only; the cooperative cancel that matters is tested against the
    # real sidecar in tests/test_job_cancel.py.
    "job.cancel": "the preview's jobs hold no work to cancel",
    # History rows in the preview are session state, rebuilt on reload, so a
    # delete has nothing persistent to remove. The sidecar's behaviour, including
    # refusing to delete a row an artifact still points at, is in pytest.
    "analysis.delete": "preview history is rebuilt per page load",
    # Renaming a fixture dataset in the preview would only survive until the next
    # reload, and the name is not what any assertion depends on.
    "dataset.rename": "the preview's datasets are fixtures, not user state",
    # It writes a directory of frames to a user path; an in-browser mock has no
    # filesystem. Covered end to end against the sidecar, including the cleanup
    # after a cancel mid-copy, in tests/test_dataset_flow.py and test_job_cancel.
    "dataset.view.materialize": "needs a filesystem the browser mock does not have",
    # A quota preview recomputes √N_g over the descriptor matrix, which the
    # preview does not load; answering it would mean shipping a second numeric
    # implementation to fake.
    "analysis.fps_quota": "would require recomputing the descriptor matrix",
}


def test_the_mock_answers_every_method_the_frontend_calls():
    # `NO_HANDLER` is not a code the sidecar can produce, so every unmocked method
    # is a branch the browser tests cannot reach - they silently exercise a
    # response the shipped app would never see.
    unmocked = sorted(frontend_called_methods() - mock_methods() - set(UNMOCKED_METHODS))
    assert not unmocked, f"preview.tsx has no handler for: {unmocked}"


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
