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
