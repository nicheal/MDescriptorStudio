"""The engine known-issues summary has to be able to fail.

scripts/verify_known_issues.py prints a verdict per documented issue, and
docs/plan/engine-known-issues.md states those verdicts as current fact. For as
long as the script returned 0 unconditionally and swallowed checker crashes, an
engine upgrade could invalidate every claim in that document without any signal.
"""

from __future__ import annotations

import pytest

import verify_known_issues as vki


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vki, "VERDICTS", {})
    monkeypatch.setattr(vki, "CRASHED", [])
    return vki


def test_every_documented_issue_has_an_expectation(isolated):
    assert set(isolated.EXPECTED) == {str(n) for n in range(1, 11)}


def test_clean_run_reports_no_drift(isolated):
    for issue, wanted in isolated.EXPECTED.items():
        isolated.record(issue, wanted, {})
    assert isolated.drift_against_expectations() == []


def test_missing_wrong_and_crashed_verdicts_are_all_drift(isolated):
    isolated.record("1", "STILL_PRESENT", {})  # regressed
    isolated.record("2", "FIXED", {})  # improved without a document update
    isolated.CRASHED.append("3")
    for issue in ("4", "5", "6", "7", "8", "9"):
        isolated.record(issue, isolated.EXPECTED[issue], {})
    isolated.record("10", isolated.EXPECTED["10"], {})

    drift = isolated.drift_against_expectations()
    assert len(drift) == 3
    assert any(drift_line.startswith("#1: observed STILL_PRESENT") for drift_line in drift)
    assert any(drift_line.startswith("#2: observed FIXED") for drift_line in drift)
    assert any(drift_line.startswith("#3: checker crashed") for drift_line in drift)


def test_an_unrecorded_issue_is_drift(isolated):
    isolated.record("1", isolated.EXPECTED["1"], {})
    drift = isolated.drift_against_expectations()
    assert len(drift) == 9  # every other documented issue stayed silent
    assert all("no verdict recorded" in line for line in drift)


def test_a_silent_child_hits_the_deadline_instead_of_hanging_the_script(monkeypatch) -> None:
    """Issue 1's symptom is a child that prints nothing at all. The old loop
    checked its deadline only after `readline()` returned, so the script hung and
    the kill in `finally` never ran; only the workflow's 45 minute timeout
    noticed. Reading through a queue gives the wait something to time out on."""
    import os
    import time

    read_fd, write_fd = os.pipe()
    silent = os.fdopen(read_fd, "r")
    started = time.monotonic()
    try:
        assert vki.read_child_lines(silent, 0.5) == ["TIMEOUT_PARENT"]
    finally:
        # Closing the write end is what lets the reader thread see EOF; the read
        # end is left to the process, because yanking it from under a blocked
        # reader is how a test turns into a flake.
        os.close(write_fd)
    assert time.monotonic() - started < 5


def test_child_output_is_read_up_to_the_end_marker() -> None:
    import io

    assert vki.read_child_lines(io.StringIO("BUILD_DONE elapsed=1\nCHILD_END\nlate\n"), 5) == [
        "BUILD_DONE elapsed=1",
        "CHILD_END",
    ]
