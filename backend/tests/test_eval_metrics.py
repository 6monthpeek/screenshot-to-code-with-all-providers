import json
import os
from pathlib import Path

from evals.runner import record_stack_compliance


def test_record_stack_compliance_writes_report(tmp_path: Path) -> None:
    report_path = record_stack_compliance(
        str(tmp_path),
        "react_tailwind",
        "gpt-5.5",
        {"a_0.html": True, "b_0.html": False},
    )

    assert os.path.basename(report_path) == "stack_compliance.json"
    with open(report_path) as file:
        report = json.load(file)

    assert report["stack"] == "react_tailwind"
    assert report["model"] == "gpt-5.5"
    assert report["total"] == 2
    assert report["compliant"] == 1
    assert report["compliance_rate"] == 0.5
    assert report["files"] == {"a_0.html": True, "b_0.html": False}


def test_record_stack_compliance_merges_incremental_runs(tmp_path: Path) -> None:
    # First (partial) run
    record_stack_compliance(
        str(tmp_path), "vue_tailwind", "gpt-5.5", {"a_0.html": False}
    )
    # Diff-mode re-run adds a file and fixes a previous failure
    report_path = record_stack_compliance(
        str(tmp_path),
        "vue_tailwind",
        "gpt-5.5",
        {"a_0.html": True, "c_0.html": True},
    )

    with open(report_path) as file:
        report = json.load(file)

    assert report["total"] == 2
    assert report["compliant"] == 2
    assert report["compliance_rate"] == 1.0
    assert report["files"]["a_0.html"] is True
    assert report["files"]["c_0.html"] is True


def test_record_stack_compliance_survives_corrupt_existing_report(
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "stack_compliance.json"
    corrupt.write_text("{not json")

    report_path = record_stack_compliance(
        str(tmp_path), "html_tailwind", "gpt-5.5", {"a_0.html": True}
    )

    with open(report_path) as file:
        report = json.load(file)

    assert report["total"] == 1
    assert report["compliant"] == 1
    assert report["compliance_rate"] == 1.0
