"""Tests for the Google Sheets-backed backlog repository.

No real network calls are made in this suite — a fake gspread client
stands in for the real one, injected via the module's client cache.
"""

from __future__ import annotations

import pytest

import ai_tech_lead.backlog_sheets_repository as sheets_mod
from ai_tech_lead.backlog_reference import BacklogReference
from ai_tech_lead.backlog_repository import BacklogValidationError
from ai_tech_lead.backlog_sheets_repository import (
    BacklogSheetLayout,
    BacklogSourceUnavailableError,
    SheetsBacklogRepository,
)
from ai_tech_lead.backlog_status import BacklogStatus
from ai_tech_lead.target_project_context import BacklogColumnContext

HEADER = [
    "ID",
    "Title",
    "Goal",
    "Problem",
    "Outcome",
    "Acceptance Criteria",
    "Scope",
    "Out of Scope",
    "Status",
    "Epic",
    "Type",
    "Priority",
    "Size",
    "Approval Required",
    "Approval Reason",
    "Evidence / Validation",
    "Notes / Cleanup Action",
]

SPREADSHEET_ID = "spreadsheet-1"
SHEET_NAME = "AI Tech Lead Backlog"
CREDENTIALS_PATH = "tests/fixtures/fake-credentials.json"


def _row(item_id, title, status="Backlog", **overrides):
    values = {
        "ID": item_id,
        "Title": title,
        "Goal": "",
        "Problem": "",
        "Outcome": "",
        "Acceptance Criteria": "",
        "Scope": "",
        "Out of Scope": "",
        "Status": status,
        "Epic": "",
        "Type": "",
        "Priority": "Medium",
        "Size": "S",
        "Approval Required": "no",
        "Approval Reason": "",
        "Evidence / Validation": "",
        "Notes / Cleanup Action": "",
    }
    values.update(overrides)
    return [values[column] for column in HEADER]


class FakeWorksheet:
    def __init__(self, values):
        self._values = values
        self.updates: list[tuple[int, int, str]] = []
        self.appended: list[list[str]] = []

    def get_all_values(self):
        return [list(row) for row in self._values]

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))
        r, c = row - 1, col - 1
        while len(self._values[r]) <= c:
            self._values[r].append("")
        self._values[r][c] = value

    def append_row(self, row_values, value_input_option="RAW"):
        self.appended.append(list(row_values))
        self._values.append(list(row_values))


class RaisingWorksheet:
    def get_all_values(self):
        raise RuntimeError("simulated Sheets API outage")


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self._worksheets = worksheets

    def worksheet(self, name):
        if name not in self._worksheets:
            raise RuntimeError(f"worksheet not found: {name}")
        return self._worksheets[name]


class FakeClient:
    def __init__(self, spreadsheets):
        self._spreadsheets = spreadsheets

    def open_by_key(self, spreadsheet_id):
        if spreadsheet_id not in self._spreadsheets:
            raise RuntimeError(f"spreadsheet not found: {spreadsheet_id}")
        return self._spreadsheets[spreadsheet_id]


def _repository(monkeypatch, worksheet, *, spreadsheet_id=SPREADSHEET_ID, sheet_name=SHEET_NAME):
    client = FakeClient({spreadsheet_id: FakeSpreadsheet({sheet_name: worksheet})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    reference = BacklogReference(
        project_key="ai-tech-lead",
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        item_id="",
    )
    return SheetsBacklogRepository(reference, credentials_path=CREDENTIALS_PATH)


def test_get_item_with_source_returns_exact_row_number_and_hash(monkeypatch):
    worksheet = FakeWorksheet(
        [HEADER, _row("ATL-001", "First item"), _row("ATL-002", "Second item")]
    )
    repo = _repository(monkeypatch, worksheet)

    record = repo.get_item_with_source("ATL-002")

    assert record.item.item_id == "ATL-002"
    assert record.item.title == "Second item"
    assert record.row_number == 3
    assert record.row_hash == sheets_mod.compute_row_hash(_row("ATL-002", "Second item"))


def test_get_item_unknown_id_raises_value_error(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "First item")])
    repo = _repository(monkeypatch, worksheet)

    with pytest.raises(ValueError, match="was not found"):
        repo.get_item("ATL-999")


def test_get_item_duplicate_id_raises_backlog_validation_error(monkeypatch):
    worksheet = FakeWorksheet(
        [HEADER, _row("ATL-001", "First item"), _row("ATL-001", "Duplicate item")]
    )
    repo = _repository(monkeypatch, worksheet)

    with pytest.raises(BacklogValidationError, match="appears 2 times"):
        repo.get_item("ATL-001")


def test_apply_fields_writes_only_allowed_columns(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "First item")])
    repo = _repository(monkeypatch, worksheet)

    updated = repo.apply_fields("ATL-001", {"Status": "Done", "Evidence / Validation": "ok"})

    assert updated.status == BacklogStatus.DONE
    status_col = HEADER.index("Status") + 1
    evidence_col = HEADER.index("Evidence / Validation") + 1
    assert (2, status_col, "Done") in worksheet.updates
    assert (2, evidence_col, "ok") in worksheet.updates


def test_apply_fields_rejects_disallowed_field(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "First item")])
    repo = _repository(monkeypatch, worksheet)

    with pytest.raises(BacklogValidationError, match="not allowed runtime update fields"):
        repo.apply_fields("ATL-001", {"Title": "New title"})

    assert worksheet.updates == []


def test_update_item_status_and_complete_item(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "First item")])
    repo = _repository(monkeypatch, worksheet)

    repo.update_item_status("ATL-001", "In Progress")
    assert repo.get_item("ATL-001").status == BacklogStatus.IN_PROGRESS

    completed = repo.complete_item("ATL-001", "Validation: all tests pass")
    assert completed.status == BacklogStatus.DONE
    assert "Evidence / Validation: all tests pass" in completed.body


def test_access_failure_raises_source_unavailable_with_no_fallback(monkeypatch):
    repo = _repository(monkeypatch, RaisingWorksheet())

    with pytest.raises(BacklogSourceUnavailableError, match="simulated Sheets API outage"):
        repo.get_item("ATL-001")


def test_unreachable_spreadsheet_raises_source_unavailable(monkeypatch):
    client = FakeClient({})  # no spreadsheets registered at all
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    reference = BacklogReference(
        project_key="ai-tech-lead",
        spreadsheet_id="missing-spreadsheet",
        sheet_name=SHEET_NAME,
        item_id="",
    )
    repo = SheetsBacklogRepository(reference, credentials_path=CREDENTIALS_PATH)

    with pytest.raises(BacklogSourceUnavailableError, match="Could not open spreadsheet"):
        repo.list_items()


def test_list_open_items_excludes_terminal_statuses(monkeypatch):
    worksheet = FakeWorksheet(
        [
            HEADER,
            _row("ATL-001", "Open item", status="Backlog"),
            _row("ATL-002", "Done item", status="Done"),
        ]
    )
    repo = _repository(monkeypatch, worksheet)

    open_ids = {item.item_id for item in repo.list_open_items()}

    assert open_ids == {"ATL-001"}


def test_add_refined_item_appends_row_with_mapped_columns(monkeypatch):
    from ai_tech_lead.backlog_repository import BacklogRefinementDraft

    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "Existing item")])
    repo = _repository(monkeypatch, worksheet)

    draft = BacklogRefinementDraft(
        item_id="ATL-002",
        title="New refined item",
        creator="tech-lead-analyst",
        item_type="Task",
        epic="Backlog Storage Cleanup",
        priority="Medium",
        size="S",
        approval_required=False,
        approval_reason="No approval needed.",
        problem="Problem text.",
        desired_outcome="Outcome text.",
        scope=["In scope item"],
        out_of_scope=["Out of scope item"],
        acceptance_criteria=["Criterion one"],
        duplicate_check_result="None found.",
        stale_check_result="Not stale.",
        already_done_check_result="Not already done.",
        research_required=False,
        research_cache_used=[],
        external_research_needed=False,
        recommended_implementation_pattern="Pattern.",
        patterns_explicitly_rejected=["Rejected pattern"],
        freshness_risk="Low.",
        implementation_guidance="Guidance.",
        approval_risk_flags=[],
    )

    result = repo.add_refined_item(draft)

    assert result.item_id == "ATL-002"
    assert len(worksheet.appended) == 1
    appended_row = worksheet.appended[0]
    assert appended_row[HEADER.index("ID")] == "ATL-002"
    assert appended_row[HEADER.index("Title")] == "New refined item"
    assert appended_row[HEADER.index("Problem")] == "Problem text."
    assert appended_row[HEADER.index("Status")] == "Backlog"


def test_add_refined_item_respects_custom_layout_columns(monkeypatch):
    from ai_tech_lead.backlog_repository import BacklogRefinementDraft

    custom_header = [
        "Work ID",
        "Work Title",
        "Goal",
        "Issue",
        "Outcome",
        "Acceptance Criteria",
        "Scope",
        "Out of Scope",
        "Workflow Status",
        "Epic",
        "Work Type",
        "Priority",
        "Size",
        "Needs Approval",
        "Approval Notes",
        "Evidence / Validation",
        "Notes / Cleanup Action",
    ]
    row = [""] * len(custom_header)
    row[custom_header.index("Work ID")] = "HUB-001"
    row[custom_header.index("Work Title")] = "Existing item"
    row[custom_header.index("Workflow Status")] = "Backlog"
    row[custom_header.index("Priority")] = "Medium"
    row[custom_header.index("Size")] = "S"
    worksheet = FakeWorksheet([custom_header, row])
    client = FakeClient({SPREADSHEET_ID: FakeSpreadsheet({SHEET_NAME: worksheet})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    reference = BacklogReference("agent-hub", SPREADSHEET_ID, SHEET_NAME, "")
    repo = SheetsBacklogRepository(
        reference,
        credentials_path=CREDENTIALS_PATH,
        layout=BacklogSheetLayout(
            columns=BacklogColumnContext(
                item_id="Work ID",
                title="Work Title",
                problem="Issue",
                status="Workflow Status",
                item_type="Work Type",
                approval_required="Needs Approval",
                approval_reason="Approval Notes",
            )
        ),
    )

    draft = BacklogRefinementDraft(
        item_id="HUB-002",
        title="New refined item",
        creator="tech-lead-analyst",
        item_type="Story",
        epic="Backlog",
        priority="High",
        size="M",
        approval_required=True,
        approval_reason="New workflow.",
        problem="Problem text.",
        desired_outcome="Outcome text.",
        scope=["Keep it small"],
        out_of_scope=["Do not broaden"],
        acceptance_criteria=["Criterion A"],
        duplicate_check_result="No duplicate found.",
        stale_check_result="No stale item found.",
        already_done_check_result="Not already done.",
        research_required=False,
        research_cache_used=[],
        external_research_needed=False,
        recommended_implementation_pattern="Reuse existing service.",
        patterns_explicitly_rejected=["Freeform prose"],
        freshness_risk="Low.",
        implementation_guidance="Stay structured.",
        approval_risk_flags=["Writes backlog items"],
    )

    repo.add_refined_item(draft)

    appended_row = worksheet.appended[0]
    assert appended_row[custom_header.index("Work ID")] == "HUB-002"
    assert appended_row[custom_header.index("Work Title")] == "New refined item"
    assert appended_row[custom_header.index("Issue")] == "Problem text."
    assert appended_row[custom_header.index("Workflow Status")] == "Backlog"


def test_dynamic_reference_to_two_different_spreadsheets(monkeypatch):
    worksheet_a = FakeWorksheet([HEADER, _row("ATL-001", "Project A item")])
    worksheet_b = FakeWorksheet([HEADER, _row("HUB-001", "Project B item")])
    client = FakeClient(
        {
            "spreadsheet-a": FakeSpreadsheet({SHEET_NAME: worksheet_a}),
            "spreadsheet-b": FakeSpreadsheet({SHEET_NAME: worksheet_b}),
        }
    )
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})

    repo_a = SheetsBacklogRepository(
        BacklogReference("a", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    repo_b = SheetsBacklogRepository(
        BacklogReference("b", "spreadsheet-b", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )

    assert repo_a.get_item("ATL-001").title == "Project A item"
    assert repo_b.get_item("HUB-001").title == "Project B item"
