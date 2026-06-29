"""Seed AI Tech Lead backlog items for data/setup/production persistence work.

Run from WSL project root:

    uv run python scripts/add_data_management_backlog_items.py

This mutates the local runtime backlog at data/backlog.sqlite3 through the
SqliteBacklogRepository boundary. It skips items with matching titles so it can
be re-run safely.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_tech_lead.backlog_repository import BacklogRefinementDraft
from ai_tech_lead.backlog_store import SqliteBacklogRepository


@dataclass(frozen=True)
class DataBacklogSpec:
    title: str
    epic: str
    priority: str
    size: str
    problem: str
    desired_outcome: str
    scope: list[str]
    out_of_scope: list[str]
    acceptance_criteria: list[str]
    implementation_guidance: str


ITEMS: tuple[DataBacklogSpec, ...] = (
    DataBacklogSpec(
        title="Add AI Tech Lead data policy and gitignore validation",
        epic="Data Management",
        priority="High",
        size="M",
        problem="AI Tech Lead mixes source seed files, generated files, local settings, and runtime SQLite state under data/.",
        desired_outcome="The repo clearly defines which data files are committed, ignored, generated, local-only, or production-persisted.",
        scope=[
            "Document data file classes with examples for prompts, settings, SQLite, logs, generated diagrams, and environment files.",
            "Add local CI tests that verify required seed files are tracked and runtime files are not tracked.",
            "Update docs/INDEX.md and AGENTS.md pointers so coding agents follow the policy.",
        ],
        out_of_scope=[
            "No broad repo restructure.",
            "No production deployment changes in this item.",
        ],
        acceptance_criteria=[
            "Data policy exists and is linked from the main docs index.",
            "CI fails clearly if required seed data is missing or runtime data is tracked.",
            "Prompt registry and safe settings example are covered by validation.",
        ],
        implementation_guidance="Start with a small docs + tests change. Do not unignore all data/.",
    ),
    DataBacklogSpec(
        title="Add AI Tech Lead first-run setup command",
        epic="Developer Experience",
        priority="High",
        size="M",
        problem="Fresh WSL clones still require manual setup knowledge for data directories, local settings, and SQLite initialization.",
        desired_outcome="A new clone can be prepared with one idempotent setup command.",
        scope=[
            "Create required data directories if missing.",
            "Copy safe example settings to ignored local settings only when missing.",
            "Initialize required SQLite stores and schemas.",
            "Print friendly next steps for local values, Telegram, model configuration, and tests.",
        ],
        out_of_scope=[
            "Do not create real credentials.",
            "Do not overwrite existing local settings without explicit approval.",
        ],
        acceptance_criteria=[
            "Command can run twice without damaging local files.",
            "Missing settings error tells the operator exactly what to run.",
            "Runbook points to the setup command.",
        ],
        implementation_guidance="Prefer a small CLI subcommand or script that uses existing config constants.",
    ),
    DataBacklogSpec(
        title="Add AI Tech Lead data doctor command",
        epic="Platform Hardening",
        priority="High",
        size="M",
        problem="Data/setup problems are discovered late, usually after startup, tests, or CI fail.",
        desired_outcome="Operator can run one command to inspect local data health before starting agents.",
        scope=[
            "Report required seed files, local settings, runtime stores, logs, generated outputs, and missing setup steps.",
            "Check that local settings exist and are ignored by Git.",
            "Check expected SQLite tables and schema versions.",
            "Warn if runtime files are staged for Git.",
        ],
        out_of_scope=[
            "No destructive cleanup by default.",
            "No automatic repair unless an explicit apply/repair mode is approved.",
        ],
        acceptance_criteria=[
            "Doctor output is friendly and concise.",
            "Failures identify exact file/path and suggested fix.",
            "Tests cover missing settings and accidentally staged runtime files.",
        ],
        implementation_guidance="Implement diagnostics first; make any repair mode a later explicit task.",
    ),
    DataBacklogSpec(
        title="Add AI Tech Lead SQLite schema versioning and migrations",
        epic="Persistence",
        priority="High",
        size="L",
        problem="Runtime SQLite stores are generated locally but lack a clear versioned migration boundary.",
        desired_outcome="Runtime stores can be initialized and upgraded safely without committing SQLite files.",
        scope=[
            "Add schema version tracking for runtime SQLite stores.",
            "Make setup initialize missing stores.",
            "Make startup fail clearly when a migration is required.",
            "Document and test migration order.",
        ],
        out_of_scope=[
            "No destructive automatic migrations.",
            "No production migration without backup instructions.",
        ],
        acceptance_criteria=[
            "Fresh clone initializes required stores.",
            "Existing store with old schema reports a clear upgrade path.",
            "Tests cover initialization and migration-required cases.",
        ],
        implementation_guidance="Keep this small and explicit. Do not add a full migration framework unless needed.",
    ),
    DataBacklogSpec(
        title="Make AI Tech Lead knowledge store path configurable",
        epic="Knowledge Store",
        priority="High",
        size="M",
        problem="knowledge_store.py has a hardcoded shared Agent Factory path with a local fallback, which is fragile for WSL and production.",
        desired_outcome="Knowledge store location is controlled by settings/environment and documented for local and production use.",
        scope=[
            "Move knowledge store path into validated settings or environment configuration.",
            "Keep shared Agent Factory store as an explicit configured option, not a hidden hardcoded default.",
            "Log the resolved store path clearly at startup.",
            "Update docs for local and production paths.",
        ],
        out_of_scope=[
            "Do not migrate existing data automatically in this item.",
            "Do not introduce a vector database.",
        ],
        acceptance_criteria=[
            "No hardcoded /mnt/e Agent Factory path is required for normal operation.",
            "Missing/unwritable knowledge store path fails clearly.",
            "Tests cover configured path and fallback behaviour.",
        ],
        implementation_guidance="Start from src/ai_tech_lead/knowledge_store.py and AppSettings.",
    ),
    DataBacklogSpec(
        title="Add knowledge store backup, restore, and compact commands",
        epic="Knowledge Store",
        priority="High",
        size="M",
        problem="knowledge_store.sqlite3 can become large and valuable, but raw runtime DBs should not be committed to Git.",
        desired_outcome="Knowledge memory is preserved through backup/restore/compact commands outside Git.",
        scope=[
            "Add command to report size, namespaces, item counts, largest records, and free pages.",
            "Add backup command that writes timestamped copies outside the repo or into a configured backup directory.",
            "Add restore command with explicit confirmation.",
            "Add optional approved compact/VACUUM command.",
        ],
        out_of_scope=[
            "No silent deletion of knowledge.",
            "No automatic retention pruning in this item.",
        ],
        acceptance_criteria=[
            "Operator can explain why the DB is large.",
            "Backup and restore are documented and tested on a small temp DB.",
            "Compact reports before/after size.",
        ],
        implementation_guidance="Treat this as production state tooling, not source-control tooling.",
    ),
    DataBacklogSpec(
        title="Add production data root and persistent storage strategy",
        epic="Production Readiness",
        priority="High",
        size="L",
        problem="Local WSL data paths are not enough for go-live; runtime DBs must survive restarts without being committed to Git.",
        desired_outcome="Production deployments use explicit persistent storage for runtime DBs, knowledge store, logs, and backups.",
        scope=[
            "Define production data root per environment.",
            "Read data root from settings/environment, not local WSL defaults.",
            "Document backup cadence and restore path.",
            "Fail startup if production data path is missing, unwritable, or ephemeral without acknowledgement.",
        ],
        out_of_scope=[
            "No cloud-specific implementation until target hosting is chosen.",
            "No automatic migration of local WSL data to production.",
        ],
        acceptance_criteria=[
            "Runbook explains local vs production data roots.",
            "Production mode does not write important runtime state inside Git checkout.",
            "Startup logs resolved data root and persistence checks.",
        ],
        implementation_guidance="Keep provider-neutral. This should work before choosing AWS/GCP/etc.",
    ),
    DataBacklogSpec(
        title="Add bounded knowledge ingestion and deduplication controls",
        epic="Knowledge Store",
        priority="High",
        size="M",
        problem="Shared knowledge storage can grow quickly if agents ingest full docs, duplicate notes, or raw tool outputs.",
        desired_outcome="Knowledge ingestion is bounded, deduplicated, and inspectable before it grows uncontrollably.",
        scope=[
            "Set maximum item size and maximum items per ingestion run.",
            "Store source metadata, timestamps, namespace, and source hash where possible.",
            "Deduplicate by source/path/hash.",
            "Report storage impact before large ingestion.",
        ],
        out_of_scope=[
            "Do not add vector DB/RAG complexity unless retrieval becomes a proven problem.",
            "Do not ingest full repositories by default.",
        ],
        acceptance_criteria=[
            "Oversized ingestion is rejected or requires approval.",
            "Duplicate source ingestion is skipped or updated deterministically.",
            "Tests cover duplicate and oversized cases.",
        ],
        implementation_guidance="Align with context discipline: offload context, but keep it bounded and inspectable.",
    ),
)


def _build_draft(item_id: str, spec: DataBacklogSpec) -> BacklogRefinementDraft:
    return BacklogRefinementDraft(
        item_id=item_id,
        title=spec.title,
        creator="Human",
        item_type="Story",
        epic=spec.epic,
        priority=spec.priority,
        size=spec.size,
        approval_required=True,
        approval_reason="Data, persistence, setup, or production behaviour affects runtime safety and must be reviewed.",
        problem=spec.problem,
        desired_outcome=spec.desired_outcome,
        scope=spec.scope,
        out_of_scope=spec.out_of_scope,
        acceptance_criteria=spec.acceptance_criteria,
        duplicate_check_result="Seeder skips an item when an existing backlog item has the same title.",
        stale_check_result="Current as of 2026-06-29 based on data/setup issues found during gitignore and prompt registry work.",
        already_done_check_result="Not confirmed done in the local runtime backlog before seeding.",
        research_required=False,
        research_cache_used=[],
        external_research_needed=False,
        recommended_implementation_pattern="Small bounded change using existing repository/settings boundaries.",
        patterns_explicitly_rejected=[
            "Committing runtime SQLite files to Git.",
            "Silent fallback paths.",
            "Broad repository rewrites.",
        ],
        freshness_risk="Low for local repository hygiene; re-check production storage details when deployment target is chosen.",
        implementation_guidance=spec.implementation_guidance,
        approval_risk_flags=[
            "Persistent runtime data",
            "Local/production setup",
            "Agent behaviour may depend on stored state",
        ],
    )


def _existing_titles(repository: SqliteBacklogRepository) -> set[str]:
    try:
        return {item.title.strip().lower() for item in repository.list_items()}
    except ValueError:
        return set()


def main() -> int:
    repository = SqliteBacklogRepository()
    existing = _existing_titles(repository)
    added = 0
    skipped = 0

    for spec in ITEMS:
        normalized_title = spec.title.strip().lower()
        if normalized_title in existing:
            print(f"SKIP existing title: {spec.title}")
            skipped += 1
            continue

        item_id = repository.next_item_id("ATL")
        item = repository.add_refined_item(_build_draft(item_id, spec))
        existing.add(normalized_title)
        added += 1
        print(f"ADD {item.item_id}: {item.title}")

    print(f"Done. Added={added} Skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
