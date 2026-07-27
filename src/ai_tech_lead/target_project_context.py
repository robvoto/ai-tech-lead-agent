"""Immutable target-project context derived from the specialist boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    """Bounded backlog identity and fetched item details for this run."""

    project_key: str
    spreadsheet_id: str
    sheet_name: str
    item_id: str
    title: str = ""
    body: str = ""

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
        )


@dataclass(frozen=True)
class TargetProjectContext:
    """AI Tech Lead's internal, immutable view of the target project."""

    project_root: str = ""
    project_key: str = ""
    project_name: str = ""
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

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project_root": self.project_root,
            "project_key": self.project_key,
            "project_name": self.project_name,
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
            resource_references=tuple(
                ResourceReferenceContext.from_payload(item)
                for item in raw_resources
                if isinstance(item, dict)
            ),
            backlog_item=backlog_item,
        )
