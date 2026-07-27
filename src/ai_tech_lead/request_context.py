"""Bound and resolve general technical requests before research."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .target_project_context import TargetProjectContext

_REFERENCE_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{2,6}\b")
_INLINE_BACKLOG_PATTERN = re.compile(r"(?im)^\s*backlog item:\s*([A-Z][A-Z0-9]{1,9}-\d{2,6})\b")
_NON_EXECUTION_PHRASES = (
    "report only",
    "do not code",
    "don't code",
    "dont code",
    "do not implement",
    "don't implement",
    "explain only",
    "review only",
)


@dataclass(frozen=True)
class RequestUnderstanding:
    intent: str
    execution_requested: bool
    detected_references: tuple[str, ...]


def understand_request(request: str) -> RequestUnderstanding:
    """Classify request intent without inventing project or resource meaning."""

    normalized = " ".join(request.strip().split())
    lowered = normalized.lower()
    execution_requested = not any(phrase in lowered for phrase in _NON_EXECUTION_PHRASES) and any(
        token in lowered
        for token in ("code ", "implement", "fix ", "build ", "change ", "update ", "remove ")
    )
    if any(token in lowered for token in ("review", "analyse", "analyze", "inspect", "report")):
        intent = "review"
    elif any(token in lowered for token in ("explain", "how", "why")) and not execution_requested:
        intent = "explanation"
    elif execution_requested:
        intent = "implementation"
    else:
        intent = "technical_task"
    return RequestUnderstanding(
        intent=intent,
        execution_requested=execution_requested,
        detected_references=tuple(dict.fromkeys(_REFERENCE_PATTERN.findall(normalized))),
    )


def resolve_request_context(
    request: str,
    *,
    target_project_context: TargetProjectContext | None = None,
) -> dict[str, Any]:
    """Resolve detected references only from explicit caller-supplied context."""

    understanding = understand_request(request)
    resolved_ids: set[str] = {match.upper() for match in _INLINE_BACKLOG_PATTERN.findall(request)}
    evidence: list[str] = []
    for item_id in sorted(resolved_ids):
        evidence.append(f"Backlog reference {item_id} was supplied inline with task context.")

    backlog = target_project_context.backlog_item if target_project_context else None
    if backlog is not None:
        item_id = backlog.item_id.strip().upper()
        if item_id:
            resolved_ids.add(item_id)
            evidence.append(f"Backlog reference {item_id} supplied by caller and fetched successfully.")
    for resource in target_project_context.resource_references if target_project_context else ():
        resource_id = resource.item_id.strip().upper()
        if resource_id:
            resolved_ids.add(resource_id)
            evidence.append(f"Resource reference {resource_id} supplied by caller.")

    unresolved = tuple(ref for ref in understanding.detected_references if ref.upper() not in resolved_ids)
    project_name = target_project_context.project_identity if target_project_context else ""
    project_root = target_project_context.project_root if target_project_context else ""
    if project_name:
        evidence.append(f"Project context supplied: {project_name}.")
    if project_root:
        evidence.append(f"Project root supplied: {project_root}.")

    bounded = request.strip()
    context_lines: list[str] = []
    if project_name:
        context_lines.append(f"Project: {project_name}")
    if project_root:
        context_lines.append(f"Project root: {project_root}")
    if backlog is not None:
        item_id = backlog.item_id.strip()
        title = backlog.title.strip()
        body = backlog.body.strip()
        context_lines.append(f"Backlog item: {item_id}{' - ' + title if title else ''}")
        if body:
            context_lines.append(body)
    if context_lines:
        bounded = f"{bounded}\n\nResolved context:\n" + "\n".join(context_lines)

    question = ""
    if unresolved:
        first = unresolved[0]
        question = f"What does {first} refer to, and where should I retrieve it from?"

    return {
        "bounded_request": bounded,
        "request_intent": understanding.intent,
        "execution_requested": understanding.execution_requested,
        "detected_references": list(understanding.detected_references),
        "resolved_project_identity": project_name,
        "resolved_project_root": project_root,
        "resolved_resource_references": sorted(resolved_ids),
        "unresolved_references": list(unresolved),
        "context_resolution_evidence": evidence,
        "clarification_question": question,
    }
