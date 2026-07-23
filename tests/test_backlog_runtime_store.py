"""Tests for the SQLite runtime-state store: snapshots, outbox, conflicts."""

from __future__ import annotations

from test_backlog_sheets_repository import (
    CREDENTIALS_PATH,
    HEADER,
    SHEET_NAME,
    SPREADSHEET_ID,
    FakeClient,
    FakeSpreadsheet,
    FakeWorksheet,
    RaisingWorksheet,
    _row,
)

import ai_tech_lead.backlog_sheets_repository as sheets_mod
from ai_tech_lead.backlog_reference import BacklogReference
from ai_tech_lead.backlog_runtime_store import (
    BacklogRuntimeStore,
    enqueue_and_flush_update,
    recover_pending_backlog_updates,
)
from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository


def _sheets_repository(monkeypatch, worksheet, *, spreadsheet_id=SPREADSHEET_ID):
    client = FakeClient({spreadsheet_id: FakeSpreadsheet({SHEET_NAME: worksheet})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    reference = BacklogReference("ai-tech-lead", spreadsheet_id, SHEET_NAME, "")
    return SheetsBacklogRepository(reference, credentials_path=CREDENTIALS_PATH)


def test_boundary_has_no_backlog_ownership_methods():
    for forbidden in ("list_items", "add_item", "update_item_status", "complete_item"):
        assert not hasattr(BacklogRuntimeStore, forbidden)


def test_save_and_get_snapshot_round_trips():
    store = BacklogRuntimeStore()
    store.save_snapshot(
        request_id="req-1",
        project_key="ai-tech-lead",
        spreadsheet_id=SPREADSHEET_ID,
        sheet_name=SHEET_NAME,
        item_id="ATL-001",
        row_data=["ATL-001", "Title"],
        row_hash="hash-1",
        fetched_at="2026-07-23T00:00:00+00:00",
    )

    snapshot = store.get_snapshot("req-1")

    assert snapshot is not None
    assert snapshot.item_id == "ATL-001"
    assert snapshot.row_hash == "hash-1"
    assert snapshot.run_status == "active"
    assert store.get_snapshot("missing") is None


def test_enqueue_and_flush_applies_when_hash_unchanged(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "First item")])
    repo = _sheets_repository(monkeypatch, worksheet)
    store = BacklogRuntimeStore()
    record = repo.get_item_with_source("ATL-001")

    status = enqueue_and_flush_update(
        runtime_store=store,
        sheets_repository=repo,
        request_id="req-1",
        item_id="ATL-001",
        update_fields={"Status": "Done"},
        expected_row_hash=record.row_hash,
        max_attempts=3,
    )

    assert status == "synced"
    applied = store.list_pending_updates(status="applied")
    assert len(applied) == 1
    assert store.list_pending_updates(status="pending") == []
    assert repo.get_item("ATL-001").status.value == "Done"


def test_enqueue_and_flush_records_conflict_on_changed_source(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "First item")])
    repo = _sheets_repository(monkeypatch, worksheet)
    store = BacklogRuntimeStore()

    status = enqueue_and_flush_update(
        runtime_store=store,
        sheets_repository=repo,
        request_id="req-1",
        item_id="ATL-001",
        update_fields={"Status": "Done"},
        expected_row_hash="stale-hash-that-does-not-match",
        max_attempts=3,
    )

    assert status == "conflict"
    pending = store.list_pending_updates(status="pending")
    abandoned = store.list_pending_updates(status="abandoned")
    assert pending == []
    assert len(abandoned) == 1
    # The Sheet must not have been touched.
    assert repo.get_item("ATL-001").status.value == "Backlog"


def test_enqueue_and_flush_leaves_pending_on_transient_failure(monkeypatch):
    repo = _sheets_repository(monkeypatch, RaisingWorksheet())
    store = BacklogRuntimeStore()

    status = enqueue_and_flush_update(
        runtime_store=store,
        sheets_repository=repo,
        request_id="req-1",
        item_id="ATL-001",
        update_fields={"Status": "Done"},
        expected_row_hash="whatever-hash",
        max_attempts=3,
    )

    assert status == "pending"
    pending = store.list_pending_updates(status="pending")
    assert len(pending) == 1
    assert pending[0].attempt_count == 1
    assert pending[0].last_error


def test_recover_pending_updates_is_bounded_and_abandons_after_max_attempts(monkeypatch):
    repo = _sheets_repository(monkeypatch, RaisingWorksheet())
    store = BacklogRuntimeStore()

    enqueue_and_flush_update(
        runtime_store=store,
        sheets_repository=repo,
        request_id="req-1",
        item_id="ATL-001",
        update_fields={"Status": "Done"},
        expected_row_hash="whatever-hash",
        max_attempts=3,
    )
    assert store.list_pending_updates(status="pending")[0].attempt_count == 1

    def factory(spreadsheet_id, sheet_name):
        return repo

    outcomes_second_attempt = recover_pending_backlog_updates(store, factory, max_per_call=20)
    assert outcomes_second_attempt == ["pending"]
    assert store.list_pending_updates(status="pending")[0].attempt_count == 2

    outcomes_third_attempt = recover_pending_backlog_updates(store, factory, max_per_call=20)
    assert outcomes_third_attempt == ["abandoned"]
    assert store.list_pending_updates(status="pending") == []
    assert len(store.list_pending_updates(status="abandoned")) == 1


def test_recover_pending_updates_respects_max_per_call(monkeypatch):
    worksheet = FakeWorksheet([HEADER, _row("ATL-001", "Item"), _row("ATL-002", "Item 2")])
    repo = _sheets_repository(monkeypatch, worksheet)
    store = BacklogRuntimeStore()

    for item_id in ("ATL-001", "ATL-002"):
        store.enqueue_pending_update(
            request_id=f"req-{item_id}",
            item_id=item_id,
            spreadsheet_id=SPREADSHEET_ID,
            sheet_name=SHEET_NAME,
            update_fields={"Status": "Done"},
            expected_row_hash="stale-so-nothing-applies",
            max_attempts=1,
        )

    def factory(spreadsheet_id, sheet_name):
        return repo

    outcomes = recover_pending_backlog_updates(store, factory, max_per_call=1)

    assert len(outcomes) == 1
    remaining_pending = store.list_pending_updates(status="pending")
    assert len(remaining_pending) == 1
