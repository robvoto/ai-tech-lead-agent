"""Google Sheets-backed backlog repository.

Google Sheets is the only canonical backlog source (see docs/INDEX.md).
This module is the live storage edge behind BacklogRepositoryProtocol —
callers must not depend on Markdown vs. Sheets being behind the boundary.

Only Status and Evidence / Validation are ever written by runtime code.
Any Sheets access failure raises BacklogSourceUnavailableError; there is
no fallback to another backlog source.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import gspread

from .backlog_reference import BacklogReference, local_backlog_reference
from .backlog_repository import (
    BacklogItem,
    BacklogRefinementDraft,
    BacklogValidationError,
    _backlog_item_listing_key,
    _normalize_validation_note,
    format_backlog_list_item,  # re-export so callers don't need two imports
    open_backlog_statuses,
    validate_backlog_refinement_draft,
)
from .backlog_status import BacklogStatus, backlog_status_choices, normalize_backlog_status

logger = logging.getLogger(__name__)

__all__ = [
    "BacklogSourceRecord",
    "BacklogSourceUnavailableError",
    "SheetsBacklogRepository",
    "compute_row_hash",
    "format_backlog_list_item",
    "repository_for",
    "repository_from_settings",
]

_SHEET_COLUMNS = [
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
_ALLOWED_UPDATE_FIELDS = {"Status", "Evidence / Validation"}
_BODY_FIELDS = [
    "Goal",
    "Problem",
    "Outcome",
    "Acceptance Criteria",
    "Scope",
    "Out of Scope",
    "Epic",
    "Type",
    "Approval Reason",
    "Evidence / Validation",
    "Notes / Cleanup Action",
]

_client_cache: dict[str, gspread.Client] = {}


class BacklogSourceUnavailableError(RuntimeError):
    """Raised when the Google Sheet cannot be reached, opened, or read."""


@dataclass(frozen=True)
class BacklogSourceRecord:
    """A parsed BacklogItem plus its exact source location, for conflict detection."""

    item: BacklogItem
    row_number: int
    row_values: list[str]
    row_hash: str
    fetched_at: str


def compute_row_hash(row_values: list[str]) -> str:
    """Deterministic hash of a raw sheet row, used to detect a changed source row."""

    return hashlib.sha256("\x1f".join(row_values).encode("utf-8")).hexdigest()


def _get_client(credentials_path: str) -> gspread.Client:
    client = _client_cache.get(credentials_path)
    if client is not None:
        return client
    try:
        client = gspread.service_account(filename=credentials_path)
    except Exception as exc:
        raise BacklogSourceUnavailableError(
            f"Could not authenticate to Google Sheets using credentials at "
            f"'{credentials_path}': {exc}"
        ) from exc
    _client_cache[credentials_path] = client
    return client


class SheetsBacklogRepository:
    """Google Sheets-backed backlog repository, scoped to one spreadsheet/sheet."""

    def __init__(self, reference: BacklogReference, *, credentials_path: str) -> None:
        self._reference = reference
        self._credentials_path = credentials_path

    @property
    def reference(self) -> BacklogReference:
        return self._reference

    def _worksheet(self):
        try:
            client = _get_client(self._credentials_path)
            spreadsheet = client.open_by_key(self._reference.spreadsheet_id)
            return spreadsheet.worksheet(self._reference.sheet_name)
        except BacklogSourceUnavailableError:
            raise
        except Exception as exc:
            raise BacklogSourceUnavailableError(
                f"Could not open spreadsheet '{self._reference.spreadsheet_id}' "
                f"sheet '{self._reference.sheet_name}': {exc}"
            ) from exc

    def _all_rows(self) -> tuple[list[str], list[list[str]]]:
        worksheet = self._worksheet()
        try:
            values = worksheet.get_all_values()
        except BacklogSourceUnavailableError:
            raise
        except Exception as exc:
            raise BacklogSourceUnavailableError(
                f"Could not read rows from spreadsheet '{self._reference.spreadsheet_id}' "
                f"sheet '{self._reference.sheet_name}': {exc}"
            ) from exc
        if not values:
            raise BacklogSourceUnavailableError(
                f"Sheet '{self._reference.sheet_name}' in spreadsheet "
                f"'{self._reference.spreadsheet_id}' is empty (no header row)."
            )
        return values[0], values[1:]

    def _header_index(self, header: list[str]) -> dict[str, int]:
        return {name.strip(): idx for idx, name in enumerate(header) if name.strip()}

    def list_items(self) -> list[BacklogItem]:
        header, rows = self._all_rows()
        index = self._header_index(header)
        items = [self._row_to_item(row, index) for row in rows if _cell(row, index, "ID")]
        if not items:
            raise ValueError(
                f"No backlog items found in spreadsheet '{self._reference.spreadsheet_id}' "
                f"sheet '{self._reference.sheet_name}'."
            )
        return items

    def list_open_items(self) -> list[BacklogItem]:
        return [item for item in self.list_items() if item.status in open_backlog_statuses()]

    def list_items_sorted(self) -> list[BacklogItem]:
        return sorted(self.list_items(), key=_backlog_item_listing_key)

    def list_open_items_sorted(self) -> list[BacklogItem]:
        return sorted(self.list_open_items(), key=_backlog_item_listing_key)

    def get_item(self, item_id: str) -> BacklogItem:
        return self.get_item_with_source(item_id).item

    def get_item_for_execution(self, item_id: str) -> BacklogItem:
        item = self.get_item(item_id)
        if item.status in {
            BacklogStatus.DONE,
            BacklogStatus.WONT_DO,
            BacklogStatus.OBSOLETE,
            BacklogStatus.DEFERRED,
        }:
            raise ValueError(
                f"Backlog item '{item.item_id}' is Done and cannot be selected for execution."
            )
        return item

    def get_item_with_source(self, item_id: str) -> BacklogSourceRecord:
        requested = item_id.strip().lower()
        header, rows = self._all_rows()
        index = self._header_index(header)
        matches = [
            (row_number, row)
            for row_number, row in enumerate(rows, start=2)
            if _cell(row, index, "ID").strip().lower() == requested
        ]
        if not matches:
            raise ValueError(
                f"Backlog item '{item_id}' was not found in spreadsheet "
                f"'{self._reference.spreadsheet_id}' sheet '{self._reference.sheet_name}'."
            )
        if len(matches) > 1:
            raise BacklogValidationError(
                f"Backlog item '{item_id}' appears {len(matches)} times in spreadsheet "
                f"'{self._reference.spreadsheet_id}' sheet '{self._reference.sheet_name}'; "
                "IDs must be unique."
            )
        row_number, row = matches[0]
        return BacklogSourceRecord(
            item=self._row_to_item(row, index),
            row_number=row_number,
            row_values=list(row),
            row_hash=compute_row_hash(row),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def next_item_id(self, prefix: str = "ATL") -> str:
        normalized_prefix = prefix.strip().upper()
        if not re.fullmatch(r"[A-Z]+", normalized_prefix):
            raise BacklogValidationError("Backlog ID prefix must contain uppercase letters only.")

        highest_number = 0
        for item in self.list_items():
            match = re.fullmatch(rf"{re.escape(normalized_prefix)}-(\d{{3}})", item.item_id)
            if match:
                highest_number = max(highest_number, int(match.group(1)))

        return f"{normalized_prefix}-{highest_number + 1:03d}"

    def update_item_status(self, item_id: str, new_status: str) -> BacklogItem:
        if not new_status.strip():
            raise ValueError("Status cannot be empty.")
        status_value = normalize_backlog_status(new_status)
        if status_value is None:
            raise ValueError(f"Status must be one of: {backlog_status_choices()}.")
        return self.apply_fields(item_id, {"Status": status_value.value})

    def complete_item(self, item_id: str, validation_note: str) -> BacklogItem:
        return self.apply_fields(
            item_id,
            {
                "Status": BacklogStatus.DONE.value,
                "Evidence / Validation": _normalize_validation_note(validation_note),
            },
        )

    def apply_fields(self, item_id: str, fields: dict[str, str]) -> BacklogItem:
        """Write only explicitly-allowed fields to an existing row, keyed by item_id.

        This is the sole write path for runtime status/evidence updates —
        used directly here and by the runtime-state outbox flush.
        """

        disallowed = sorted(set(fields) - _ALLOWED_UPDATE_FIELDS)
        if disallowed:
            raise BacklogValidationError(
                f"Fields {disallowed} are not allowed runtime update fields. "
                f"Allowed fields: {sorted(_ALLOWED_UPDATE_FIELDS)}."
            )

        normalized_id = item_id.strip().upper()
        record = self.get_item_with_source(normalized_id)
        header, _ = self._all_rows()
        index = self._header_index(header)
        worksheet = self._worksheet()
        for field_name, value in fields.items():
            column = index.get(field_name)
            if column is None:
                raise BacklogSourceUnavailableError(
                    f"Sheet '{self._reference.sheet_name}' has no '{field_name}' column."
                )
            try:
                worksheet.update_cell(record.row_number, column + 1, value)
            except Exception as exc:
                raise BacklogSourceUnavailableError(
                    f"Failed to write '{field_name}' for '{normalized_id}': {exc}"
                ) from exc

        return self.get_item(normalized_id)

    def add_refined_item(self, draft: BacklogRefinementDraft) -> BacklogItem:
        validate_backlog_refinement_draft(
            draft,
            existing_ids={item.item_id for item in self.list_items()},
        )
        header, _ = self._all_rows()
        index = self._header_index(header)
        row = [""] * len(header)
        for field_name, value in _refinement_draft_to_columns(draft).items():
            column = index.get(field_name)
            if column is not None:
                row[column] = value

        worksheet = self._worksheet()
        try:
            worksheet.append_row(row, value_input_option="RAW")
        except Exception as exc:
            raise BacklogSourceUnavailableError(
                f"Failed to append backlog item '{draft.item_id}' to spreadsheet "
                f"'{self._reference.spreadsheet_id}' sheet '{self._reference.sheet_name}': {exc}"
            ) from exc
        return self.get_item(draft.item_id)

    def _row_to_item(self, row: list[str], index: dict[str, int]) -> BacklogItem:
        item_id = _cell(row, index, "ID").strip()
        raw_status = _cell(row, index, "Status").strip()
        status = normalize_backlog_status(raw_status) if raw_status else BacklogStatus.BACKLOG
        if raw_status and status is None:
            raise BacklogValidationError(
                f"Unknown backlog status '{raw_status}' for item '{item_id}'. "
                f"Allowed statuses: {backlog_status_choices()}."
            )
        approval_required = _cell(row, index, "Approval Required").strip().lower() in {
            "yes",
            "true",
            "1",
        }
        body_lines = [
            f"{field_name}: {value}"
            for field_name in _BODY_FIELDS
            if (value := _cell(row, index, field_name).strip())
        ]
        return BacklogItem(
            item_id=item_id,
            title=_cell(row, index, "Title").strip(),
            body="\n".join(body_lines),
            priority=_cell(row, index, "Priority").strip(),
            complexity=_cell(row, index, "Size").strip(),
            created_date="",
            interrupt_before_implementation=approval_required,
            status=status or BacklogStatus.BACKLOG,
        )


def repository_from_settings(settings) -> SheetsBacklogRepository:
    """Build the live Sheets repository for this process's own configured backlog."""

    reference = local_backlog_reference(settings, item_id="")
    return SheetsBacklogRepository(
        reference,
        credentials_path=settings.backlog_google_credentials_path,
    )


def repository_for(
    spreadsheet_id: str,
    sheet_name: str,
    *,
    credentials_path: str,
) -> SheetsBacklogRepository:
    """Build a repository for an arbitrary spreadsheet/sheet (e.g. bounded recovery,
    where pending updates may span multiple projects)."""

    reference = BacklogReference(
        project_key="",
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        item_id="",
    )
    return SheetsBacklogRepository(reference, credentials_path=credentials_path)


def _cell(row: list[str], index: dict[str, int], field_name: str) -> str:
    column = index.get(field_name)
    if column is None or column >= len(row):
        return ""
    return row[column]


def _refinement_draft_to_columns(draft: BacklogRefinementDraft) -> dict[str, str]:
    return {
        "ID": draft.item_id,
        "Title": draft.title.strip(),
        "Problem": draft.problem.strip(),
        "Outcome": draft.desired_outcome.strip(),
        "Acceptance Criteria": "\n".join(item.strip() for item in draft.acceptance_criteria),
        "Scope": "\n".join(item.strip() for item in draft.scope),
        "Out of Scope": "\n".join(item.strip() for item in draft.out_of_scope),
        "Status": BacklogStatus.BACKLOG.value,
        "Epic": draft.epic.strip(),
        "Type": draft.item_type.strip(),
        "Priority": draft.priority.strip(),
        "Size": draft.size.strip(),
        "Approval Required": "yes" if draft.approval_required else "no",
        "Approval Reason": draft.approval_reason.strip(),
    }
