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
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import gspread
from gspread.exceptions import APIError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

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
from .target_project_context import BacklogColumnContext

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

@dataclass(frozen=True)
class BacklogSheetLayout:
    """Column layout for one project's backlog sheet."""

    columns: BacklogColumnContext = BacklogColumnContext()

    @property
    def sheet_columns(self) -> list[str]:
        return [
            self.columns.item_id,
            self.columns.title,
            self.columns.goal,
            self.columns.problem,
            self.columns.outcome,
            self.columns.acceptance_criteria,
            self.columns.scope,
            self.columns.out_of_scope,
            self.columns.status,
            self.columns.epic,
            self.columns.item_type,
            self.columns.priority,
            self.columns.size,
            self.columns.approval_required,
            self.columns.approval_reason,
            self.columns.evidence_validation,
            self.columns.notes_cleanup_action,
        ]

    @property
    def allowed_update_fields(self) -> set[str]:
        return {self.columns.status, self.columns.evidence_validation}

    @property
    def body_fields(self) -> list[str]:
        return [
            self.columns.goal,
            self.columns.problem,
            self.columns.outcome,
            self.columns.acceptance_criteria,
            self.columns.scope,
            self.columns.out_of_scope,
            self.columns.epic,
            self.columns.item_type,
            self.columns.approval_reason,
            self.columns.evidence_validation,
            self.columns.notes_cleanup_action,
        ]


DEFAULT_SHEETS_LAYOUT = BacklogSheetLayout()

_client_cache: dict[str, gspread.Client] = {}
_SHEETS_READ_MAX_ATTEMPTS = 2
_SHEETS_READ_RETRY_DELAY_SECONDS = 0.05


class BacklogSourceUnavailableError(RuntimeError):
    """Raised when the Google Sheet cannot be reached, opened, or read."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


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


def _is_transient_sheet_exception(error: Exception) -> bool:
    """Return whether a Sheets failure is safe for one bounded read retry."""

    if isinstance(error, (FileNotFoundError, PermissionError, ValueError, KeyError)):
        return False
    if isinstance(error, (RequestsConnectionError, RequestsTimeout, TimeoutError, socket.timeout)):
        return True
    if isinstance(error, APIError):
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            return False
        return status_code in {408, 429} or 500 <= status_code <= 599
    return False


def _get_client(credentials_path: str) -> gspread.Client:
    client = _client_cache.get(credentials_path)
    if client is not None:
        return client
    try:
        client = gspread.service_account(filename=credentials_path)
    except Exception as exc:
        raise BacklogSourceUnavailableError(
            f"Could not authenticate to Google Sheets using credentials at "
            f"'{credentials_path}': {exc}",
            retryable=_is_transient_sheet_exception(exc),
        ) from exc
    _client_cache[credentials_path] = client
    return client


class SheetsBacklogRepository:
    """Google Sheets-backed backlog repository, scoped to one spreadsheet/sheet."""

    def __init__(
        self,
        reference: BacklogReference,
        *,
        credentials_path: str,
        layout: BacklogSheetLayout | None = None,
    ) -> None:
        self._reference = reference
        self._credentials_path = credentials_path
        self._layout = layout or DEFAULT_SHEETS_LAYOUT

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
                f"sheet '{self._reference.sheet_name}': {exc}",
                retryable=_is_transient_sheet_exception(exc),
            ) from exc

    def _all_rows(self) -> tuple[list[str], list[list[str]]]:
        for attempt in range(1, _SHEETS_READ_MAX_ATTEMPTS + 1):
            try:
                worksheet = self._worksheet()
                try:
                    values = worksheet.get_all_values()
                except BacklogSourceUnavailableError:
                    raise
                except Exception as exc:
                    raise BacklogSourceUnavailableError(
                        f"Could not read rows from spreadsheet '{self._reference.spreadsheet_id}' "
                        f"sheet '{self._reference.sheet_name}': {exc}",
                        retryable=_is_transient_sheet_exception(exc),
                    ) from exc
                if not values:
                    raise BacklogSourceUnavailableError(
                        f"Sheet '{self._reference.sheet_name}' in spreadsheet "
                        f"'{self._reference.spreadsheet_id}' is empty (no header row)."
                    )
                return values[0], values[1:]
            except BacklogSourceUnavailableError as exc:
                if not exc.retryable or attempt >= _SHEETS_READ_MAX_ATTEMPTS:
                    logger.error(
                        "Google Sheets backlog read failed spreadsheet=%s sheet=%s "
                        "attempt=%s/%s: %s",
                        self._reference.spreadsheet_id,
                        self._reference.sheet_name,
                        attempt,
                        _SHEETS_READ_MAX_ATTEMPTS,
                        exc,
                    )
                    raise
                logger.warning(
                    "Transient Google Sheets backlog read failure spreadsheet=%s sheet=%s "
                    "attempt=%s/%s; retrying: %s",
                    self._reference.spreadsheet_id,
                    self._reference.sheet_name,
                    attempt,
                    _SHEETS_READ_MAX_ATTEMPTS,
                    exc,
                )
                time.sleep(_SHEETS_READ_RETRY_DELAY_SECONDS)

        raise AssertionError("unreachable bounded Google Sheets read retry loop")

    def _header_index(self, header: list[str]) -> dict[str, int]:
        return {name.strip(): idx for idx, name in enumerate(header) if name.strip()}

    def list_items(self) -> list[BacklogItem]:
        header, rows = self._all_rows()
        index = self._header_index(header)
        id_column = self._layout.columns.item_id
        items = [self._row_to_item(row, index) for row in rows if _cell(row, index, id_column)]
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
            if _cell(row, index, self._layout.columns.item_id).strip().lower() == requested
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
        return self.apply_fields(item_id, {self._layout.columns.status: status_value.value})

    def complete_item(self, item_id: str, validation_note: str) -> BacklogItem:
        return self.apply_fields(
            item_id,
            {
                self._layout.columns.status: BacklogStatus.DONE.value,
                self._layout.columns.evidence_validation: _normalize_validation_note(
                    validation_note
                ),
            },
        )

    def apply_fields(self, item_id: str, fields: dict[str, str]) -> BacklogItem:
        """Write only explicitly-allowed fields to an existing row, keyed by item_id.

        This is the sole write path for runtime status/evidence updates —
        used directly here and by the runtime-state outbox flush.
        """

        disallowed = sorted(set(fields) - self._layout.allowed_update_fields)
        if disallowed:
            raise BacklogValidationError(
                f"Fields {disallowed} are not allowed runtime update fields. "
                f"Allowed fields: {sorted(self._layout.allowed_update_fields)}."
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
        for field_name, value in _refinement_draft_to_columns(draft, self._layout).items():
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
        item_id = _cell(row, index, self._layout.columns.item_id).strip()
        raw_status = _cell(row, index, self._layout.columns.status).strip()
        status = normalize_backlog_status(raw_status) if raw_status else BacklogStatus.BACKLOG
        if raw_status and status is None:
            raise BacklogValidationError(
                f"Unknown backlog status '{raw_status}' for item '{item_id}'. "
                f"Allowed statuses: {backlog_status_choices()}."
            )
        approval_required = _cell(
            row, index, self._layout.columns.approval_required
        ).strip().lower() in {
            "yes",
            "true",
            "1",
        }
        body_lines = [
            f"{field_name}: {value}"
            for field_name in self._layout.body_fields
            if (value := _cell(row, index, field_name).strip())
        ]
        return BacklogItem(
            item_id=item_id,
            title=_cell(row, index, self._layout.columns.title).strip(),
            body="\n".join(body_lines),
            priority=_cell(row, index, self._layout.columns.priority).strip(),
            complexity=_cell(row, index, self._layout.columns.size).strip(),
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
        layout=DEFAULT_SHEETS_LAYOUT,
    )


def repository_for(
    spreadsheet_id: str,
    sheet_name: str,
    *,
    credentials_path: str,
    layout: BacklogSheetLayout | None = None,
) -> SheetsBacklogRepository:
    """Build a repository for an arbitrary spreadsheet/sheet (e.g. bounded recovery,
    where pending updates may span multiple projects)."""

    reference = BacklogReference(
        project_key="",
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        item_id="",
    )
    return SheetsBacklogRepository(reference, credentials_path=credentials_path, layout=layout)


def _cell(row: list[str], index: dict[str, int], field_name: str) -> str:
    column = index.get(field_name)
    if column is None or column >= len(row):
        return ""
    return row[column]


def _refinement_draft_to_columns(
    draft: BacklogRefinementDraft,
    layout: BacklogSheetLayout,
) -> dict[str, str]:
    return {
        layout.columns.item_id: draft.item_id,
        layout.columns.title: draft.title.strip(),
        layout.columns.problem: draft.problem.strip(),
        layout.columns.outcome: draft.desired_outcome.strip(),
        layout.columns.acceptance_criteria: "\n".join(
            item.strip() for item in draft.acceptance_criteria
        ),
        layout.columns.scope: "\n".join(item.strip() for item in draft.scope),
        layout.columns.out_of_scope: "\n".join(item.strip() for item in draft.out_of_scope),
        layout.columns.status: BacklogStatus.BACKLOG.value,
        layout.columns.epic: draft.epic.strip(),
        layout.columns.item_type: draft.item_type.strip(),
        layout.columns.priority: draft.priority.strip(),
        layout.columns.size: draft.size.strip(),
        layout.columns.approval_required: "yes" if draft.approval_required else "no",
        layout.columns.approval_reason: draft.approval_reason.strip(),
    }
