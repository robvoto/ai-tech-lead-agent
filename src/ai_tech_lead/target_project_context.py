"""Immutable target-project context derived from the specialist boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_CONTEXT_SCHEMA_VERSION = 1
SUPPORTED_PROJECT_CONTEXT_SCHEMA_VERSIONS = {1}


def resolve_universal_project_context(task_input: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Resolve project_root/references from Agent Hub's universal wire context.

    Mirrors Agent Factory's AF-054 `ProjectContext` shape (`schema_version` +
    `project_root` + `references`, nested under a `project_context` key) —
    inlined here rather than imported, matching Factory's own stated design:
    that module is a generation-time concern for Factory, not a shared
    runtime dependency any specialist should import or match exactly.

    Agent Hub does not send that nested shape yet; it sends flat top-level
    `project_root`/`references` (implicitly schema_version 1). Both are
    accepted so there is exactly one resolved outcome regardless of which
    shape a caller uses — not two permanent competing paths. When both a
    nested and a flat `project_root` are supplied, they must agree.
    """

    raw = task_input.get("project_context")
    if raw is None:
        return task_input.get("project_root"), list(task_input.get("references") or [])

    if not isinstance(raw, dict):
        raise ValueError("project_context must be a JSON object.")

    schema_version = raw.get("schema_version", PROJECT_CONTEXT_SCHEMA_VERSION)
    if schema_version not in SUPPORTED_PROJECT_CONTEXT_SCHEMA_VERSIONS:
        raise ValueError(
            f"project_context.schema_version={schema_version!r} is not supported by "
            f"AI Tech Lead (supports {sorted(SUPPORTED_PROJECT_CONTEXT_SCHEMA_VERSIONS)})."
        )

    context_project_root = raw.get("project_root")
    top_level_project_root = task_input.get("project_root")
    if context_project_root is not None and top_level_project_root is not None:
        context_resolved = str(Path(str(context_project_root)).resolve())
        top_level_resolved = str(Path(str(top_level_project_root)).resolve())
        if context_resolved != top_level_resolved:
            raise ValueError(
                "project_context.project_root does not match top-level project_root. "
                "Supply only one target project root, or make them identical."
            )

    project_root = context_project_root if context_project_root is not None else top_level_project_root

    references = raw.get("references")
    if references is None:
        references = task_input.get("references") or []
    if not isinstance(references, list):
        raise ValueError("project_context.references must be a list.")

    return project_root, list(references)


@dataclass(frozen=True)
class BacklogColumnContext:
    """Project-specific backlog column names, when they differ from ATL defaults."""

    item_id: str = "ID"
    title: str = "Title"
    goal: str = "Goal"
    problem: str = "Problem"
    outcome: str = "Outcome"
    acceptance_criteria: str = "Acceptance Criteria"
    scope: str = "Scope"
    out_of_scope: str = "Out of Scope"
    status: str = "Status"
    epic: str = "Epic"
    item_type: str = "Type"
    priority: str = "Priority"
    size: str = "Size"
    approval_required: str = "Approval Required"
    approval_reason: str = "Approval Reason"
    evidence_validation: str = "Evidence / Validation"
    notes_cleanup_action: str = "Notes / Cleanup Action"

    def to_payload(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "goal": self.goal,
            "problem": self.problem,
            "outcome": self.outcome,
            "acceptance_criteria": self.acceptance_criteria,
            "scope": self.scope,
            "out_of_scope": self.out_of_scope,
            "status": self.status,
            "epic": self.epic,
            "item_type": self.item_type,
            "priority": self.priority,
            "size": self.size,
            "approval_required": self.approval_required,
            "approval_reason": self.approval_reason,
            "evidence_validation": self.evidence_validation,
            "notes_cleanup_action": self.notes_cleanup_action,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> BacklogColumnContext:
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ValueError("backlog columns must be an object")
        values: dict[str, str] = {}
        for field_name in cls.__dataclass_fields__:
            raw_value = payload.get(field_name)
            if raw_value is None:
                continue
            value = str(raw_value).strip()
            if not value:
                raise ValueError(f"backlog columns.{field_name} cannot be empty")
            values[field_name] = value
        return cls(**values)


@dataclass(frozen=True)
class BacklogProjectContext:
    """Project-specific backlog repository configuration for authoring/refinement."""

    project_key: str
    spreadsheet_id: str
    sheet_name: str
    item_id_prefix: str = ""
    columns: BacklogColumnContext = BacklogColumnContext()

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project_key": self.project_key,
            "spreadsheet_id": self.spreadsheet_id,
            "sheet_name": self.sheet_name,
            "columns": self.columns.to_payload(),
        }
        if self.item_id_prefix:
            payload["item_id_prefix"] = self.item_id_prefix
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> BacklogProjectContext | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("backlog project context must be an object")
        project_key = str(payload.get("project_key", "")).strip()
        spreadsheet_id = str(payload.get("spreadsheet_id", "")).strip()
        sheet_name = str(payload.get("sheet_name", "")).strip()
        if not spreadsheet_id or not sheet_name:
            raise ValueError(
                "backlog project context must include spreadsheet_id and sheet_name"
            )
        return cls(
            project_key=project_key,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            item_id_prefix=str(payload.get("item_id_prefix", "")).strip().upper(),
            columns=BacklogColumnContext.from_payload(payload.get("columns")),
        )


@dataclass(frozen=True)
class ResourceReferenceContext:
    """One caller-supplied project/resource reference."""

    item_id: str
    title: str = ""

    def to_payload(self) -> dict[str, str]:
        payload = {"item_id": self.item_id}
        if self.title:
            payload["title"] = self.title
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ResourceReferenceContext:
        item_id = str(payload.get("item_id", "")).strip()
        if not item_id:
            raise ValueError("resource reference item_id cannot be empty")
        return cls(item_id=item_id, title=str(payload.get("title", "")).strip())


@dataclass(frozen=True)
class BacklogItemContext:
    """Bounded backlog identity and fetched item details for this run.

    ``row_hash``/``fetched_at`` capture the exact source row at fetch time —
    whichever fetch path populated this (an explicit ``backlog_reference`` up
    front, or an in-graph lookup against an already-known backlog location).
    Completion sync (``_sync_backlog_completion`` in agent_task_runner.py)
    reuses ``row_hash`` as the optimistic-concurrency baseline regardless of
    which path produced it, so there is exactly one sync mechanism.
    """

    project_key: str
    spreadsheet_id: str
    sheet_name: str
    item_id: str
    title: str = ""
    body: str = ""
    row_hash: str = ""
    fetched_at: str = ""

    def to_payload(self) -> dict[str, str]:
        payload = {
            "project_key": self.project_key,
            "spreadsheet_id": self.spreadsheet_id,
            "sheet_name": self.sheet_name,
            "item_id": self.item_id,
        }
        if self.title:
            payload["title"] = self.title
        if self.body:
            payload["body"] = self.body
        if self.row_hash:
            payload["row_hash"] = self.row_hash
        if self.fetched_at:
            payload["fetched_at"] = self.fetched_at
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BacklogItemContext:
        item_id = str(payload.get("item_id", "")).strip()
        spreadsheet_id = str(payload.get("spreadsheet_id", "")).strip()
        sheet_name = str(payload.get("sheet_name", "")).strip()
        if not item_id or not spreadsheet_id or not sheet_name:
            raise ValueError("backlog context must include item_id, spreadsheet_id, and sheet_name")
        return cls(
            project_key=str(payload.get("project_key", "")).strip(),
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            item_id=item_id,
            title=str(payload.get("title", "")).strip(),
            body=str(payload.get("body", "")).strip(),
            row_hash=str(payload.get("row_hash", "")).strip(),
            fetched_at=str(payload.get("fetched_at", "")).strip(),
        )


@dataclass(frozen=True)
class TargetProjectContext:
    """AI Tech Lead's internal, immutable view of the target project."""

    project_root: str = ""
    project_key: str = ""
    project_name: str = ""
    backlog_project: BacklogProjectContext | None = None
    resource_references: tuple[ResourceReferenceContext, ...] = ()
    backlog_item: BacklogItemContext | None = None

    @property
    def project_identity(self) -> str:
        return self.project_name or self.project_key

    @property
    def has_explicit_context(self) -> bool:
        return bool(
            self.project_root
            or self.project_key
            or self.project_name
            or self.backlog_project is not None
            or self.resource_references
            or self.backlog_item is not None
        )

    def require_project_root(self, purpose: str) -> Path:
        """Return the validated target root, or fail clearly if none was supplied."""

        if not self.project_root:
            raise ValueError(
                f"Target project root is required for {purpose}, but the specialist boundary "
                "did not supply a valid project_root."
            )
        return Path(self.project_root).resolve()

    def require_backlog_project(self, purpose: str) -> BacklogProjectContext:
        if self.backlog_project is None:
            raise ValueError(
                f"Backlog project configuration is required for {purpose}, but the specialist "
                "boundary did not supply it in the target-project context."
            )
        return self.backlog_project

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project_root": self.project_root,
            "project_key": self.project_key,
            "project_name": self.project_name,
            "backlog_project": (
                self.backlog_project.to_payload() if self.backlog_project is not None else None
            ),
            "resource_references": [item.to_payload() for item in self.resource_references],
        }
        payload["backlog_item"] = self.backlog_item.to_payload() if self.backlog_item else None
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> TargetProjectContext | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("target_project_context payload must be an object")

        raw_resources = payload.get("resource_references") or []
        if not isinstance(raw_resources, list):
            raise ValueError("target_project_context.resource_references must be a list")

        raw_backlog = payload.get("backlog_item")
        backlog_item = None
        if raw_backlog is not None:
            if not isinstance(raw_backlog, dict):
                raise ValueError("target_project_context.backlog_item must be an object")
            backlog_item = BacklogItemContext.from_payload(raw_backlog)

        return cls(
            project_root=str(payload.get("project_root", "")).strip(),
            project_key=str(payload.get("project_key", "")).strip(),
            project_name=str(payload.get("project_name", "")).strip(),
            backlog_project=BacklogProjectContext.from_payload(payload.get("backlog_project")),
            resource_references=tuple(
                ResourceReferenceContext.from_payload(item)
                for item in raw_resources
                if isinstance(item, dict)
            ),
            backlog_item=backlog_item,
        )
