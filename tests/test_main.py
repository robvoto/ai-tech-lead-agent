from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest
from helpers import valid_settings_dict

import ai_tech_lead.main as main_module
from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.run_audit_store import record_run_audit_summary


def test_main_starts_admin_and_telegram_when_enabled(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started_threads: list[str] = []
    exported_settings: list[object] = []
    settings = parse_settings(valid_settings_dict())

    class FakeThread:
        def __init__(self, target, name):
            self._target = target
            self.name = name

        def start(self):
            started_threads.append(self.name)

        def join(self):
            return None

    monkeypatch.setattr(main_module, "Thread", FakeThread)
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(main_module, "initialize_database", lambda: "db.sqlite")
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        main_module, "export_graph_diagrams", lambda *, settings: exported_settings.append(settings)
    )
    monkeypatch.setattr(main_module, "run_admin_server", lambda: None)
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type("Args", (), {"debug": False, "reload": False})(),
    )

    caplog.set_level(logging.INFO)
    main_module.main()

    assert started_threads == ["admin-server", "telegram-operator"]
    assert exported_settings == [settings]
    assert exported_settings  # export_graph_diagrams was called
    assert f"Default project root (hardcoded for now): {PROJECT_ROOT}" in caplog.text
    assert (
        f"Target project root from settings: {PROJECT_ROOT} "
        "(current default is hardcoded above)"
        in caplog.text
    )


def test_main_starts_admin_only_when_telegram_disabled(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started_threads: list[str] = []
    exported_settings: list[object] = []
    settings = replace(parse_settings(valid_settings_dict()), telegram_enabled=False)

    class FakeThread:
        def __init__(self, target, name):
            self._target = target
            self.name = name

        def start(self):
            started_threads.append(self.name)

        def join(self):
            return None

    monkeypatch.setattr(main_module, "Thread", FakeThread)
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(main_module, "initialize_database", lambda: "db.sqlite")
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        main_module, "export_graph_diagrams", lambda *, settings: exported_settings.append(settings)
    )
    monkeypatch.setattr(main_module, "run_admin_server", lambda: None)
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type("Args", (), {"debug": False, "reload": False})(),
    )

    caplog.set_level(logging.INFO)
    main_module.main()

    assert started_threads == ["admin-server"]
    assert exported_settings == [settings]
    assert exported_settings  # export_graph_diagrams was called


def test_main_admin_mode_runs_only_admin_screen(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started_admin_calls: list[tuple[str, int, object]] = []
    exported_settings: list[object] = []
    settings = parse_settings(valid_settings_dict())

    def fail_if_called() -> str:
        raise AssertionError("initialize_database should not run in admin-only mode")

    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(main_module, "initialize_database", fail_if_called)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        main_module, "export_graph_diagrams", lambda *, settings: exported_settings.append(settings)
    )
    monkeypatch.setattr(
        main_module,
        "run_admin_server",
        lambda host, port, settings_path: started_admin_calls.append((host, port, settings_path)),
    )
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type("Args", (), {"debug": False, "reload": False, "admin": True})(),
    )

    caplog.set_level(logging.INFO)
    exit_code = main_module.main()

    assert exit_code == 0
    assert started_admin_calls == [
        ("127.0.0.1", 8766, main_module.SETTINGS_PATH),
    ]
    assert exported_settings == [settings]
    assert exported_settings  # export_graph_diagrams was called


def test_main_refreshes_graph_diagrams_in_debug_mode(monkeypatch) -> None:
    started_threads: list[str] = []
    exported_settings: list[object] = []
    settings = parse_settings(valid_settings_dict())

    class FakeThread:
        def __init__(self, target, name):
            self._target = target
            self.name = name

        def start(self):
            started_threads.append(self.name)

        def join(self):
            return None

    monkeypatch.setattr(main_module, "Thread", FakeThread)
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(main_module, "initialize_database", lambda: "db.sqlite")
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        main_module, "export_graph_diagrams", lambda *, settings: exported_settings.append(settings)
    )
    monkeypatch.setattr(main_module, "run_admin_server", lambda: None)
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type("Args", (), {"debug": True, "reload": False})(),
    )

    main_module.main()

    assert exported_settings == [settings]
    assert started_threads == ["admin-server", "telegram-operator"]


def test_run_audit_command_prints_compact_receipt(capsys) -> None:
    record_run_audit_summary(
        request_id="cli-audit-test",
        thread_id="thread-cli",
        state={"request": "Inspect the receipt."},
        result={"status": "success", "result_kind": "instruction_package"},
        settings=parse_settings(valid_settings_dict()),
    )

    assert main_module._run_run_audit("cli-audit-test") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["request_id"] == "cli-audit-test"
    assert output["result"]["kind"] == "instruction_package"


def test_main_setup_mode_bootstraps_workspace(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(main_module, "initialize_database", lambda: "db.sqlite")
    monkeypatch.setattr(main_module, "bootstrap_workspace", lambda: ["setup complete"])
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type("Args", (), {"debug": False, "reload": False, "command": "setup"})(),
    )

    exit_code = main_module.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "setup complete" in captured.out


def test_main_doctor_mode_reports_workspace_health(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(
        main_module,
        "doctor_workspace",
        lambda: type("Report", (), {"ok": True, "lines": ["OK: healthy"]})(),
    )
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type("Args", (), {"debug": False, "reload": False, "command": "doctor"})(),
    )

    exit_code = main_module.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "OK: healthy" in captured.out


def test_main_bootstrap_project_pack_mode_writes_starter_pack(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(
        main_module,
        "bootstrap_project_pack",
        lambda target_root, *, overwrite=False: [
            f"bootstrapped {target_root}",
            f"overwrite={overwrite}",
        ],
    )
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type(
            "Args",
            (),
            {
                "debug": False,
                "reload": False,
                "command": "bootstrap-project-pack",
                "target_root": "/tmp/target-repo",
                "overwrite": True,
            },
        )(),
    )

    exit_code = main_module.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "bootstrapped /tmp/target-repo" in captured.out
    assert "overwrite=True" in captured.out


def test_main_knowledge_store_stats_reports_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_module, "configure_logging", lambda *, debug=False: None)
    monkeypatch.setattr(
        main_module,
        "_load_settings_for_startup",
        lambda: type(
            "Settings",
            (),
            {
                "project_root": str(main_module.PROJECT_ROOT),
                "knowledge_store_path": "data/knowledge_store.sqlite3",
            },
        )(),
    )
    monkeypatch.setattr(
        main_module,
        "get_knowledge_store_statistics",
        lambda _path: {
            "path": "/tmp/knowledge_store.sqlite3",
            "exists": True,
            "item_count": 7,
            "namespace_count": 3,
            "size_bytes": 1234,
        },
    )
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type(
            "Args",
            (),
            {
                "debug": False,
                "reload": False,
                "command": "knowledge-store",
                "knowledge_command": "stats",
            },
        )(),
    )

    exit_code = main_module.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Path: /tmp/knowledge_store.sqlite3" in captured.out
    assert "Items: 7" in captured.out


def test_main_manifest_mode_prints_compact_handshake(monkeypatch, capsys) -> None:
    def fail_if_called(*, debug: bool = False) -> None:
        raise AssertionError("configure_logging should not run for manifest")

    monkeypatch.setattr(main_module, "configure_logging", fail_if_called)
    monkeypatch.setattr(
        main_module,
        "_load_settings_for_startup",
        lambda: parse_settings(valid_settings_dict()),
    )
    monkeypatch.setattr(
        main_module,
        "_parse_args",
        lambda: type(
            "Args",
            (),
            {"debug": False, "reload": False, "command": "manifest"},
        )(),
    )

    exit_code = main_module.main()

    captured = capsys.readouterr()
    manifest = json.loads(captured.out)

    assert exit_code == 0
    assert "\n" not in captured.out.strip()
    assert manifest["agent_id"] == "ai-tech-lead"
    assert manifest["entrypoints"]["manifest"] == "manifest"
    assert manifest["manifest_hash"]


def test_admin_bind_address_prefers_settings_values() -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        admin_bind_host="0.0.0.0",
        admin_bind_port=9000,
    )

    assert main_module._admin_bind_address(settings) == ("0.0.0.0", 9000)
    assert main_module._admin_bind_address(None) == ("127.0.0.1", 8766)


def test_run_with_reload_restarts_on_source_change(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshots = iter(
        [
            {"src/file.py": 1},
            {"src/file.py": 2},
            {"src/file.py": 2},
        ]
    )
    launched_processes: list[object] = []

    class FakeProcess:
        def __init__(self, command, cwd, poll_values: list[int | None]) -> None:
            self.command = command
            self.cwd = cwd
            self._poll_values = poll_values
            self.terminated = False
            self.killed = False

        def poll(self):
            if self.terminated:
                return 0
            if self._poll_values:
                return self._poll_values.pop(0)
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def fake_popen(command, cwd):
        poll_values = [None] if not launched_processes else [None, 0]
        process = FakeProcess(command, cwd, poll_values)
        launched_processes.append(process)
        return process

    monkeypatch.setattr(main_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(main_module.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main_module, "_reload_watched_paths", lambda: (main_module.PROJECT_ROOT / "src",)
    )
    monkeypatch.setattr(main_module, "_reload_snapshot", lambda _paths: next(snapshots))
    monkeypatch.setattr(main_module.sys, "executable", "python")

    caplog.set_level(logging.INFO)
    exit_code = main_module._run_with_reload(["--debug"])

    assert exit_code == 0
    assert len(launched_processes) == 2
    assert launched_processes[0].terminated is True
    assert launched_processes[0].command == ["python", "-m", "ai_tech_lead", "--debug"]
    assert launched_processes[0].cwd == main_module.PROJECT_ROOT
    assert "Hot reload enabled; watching" in caplog.text
    assert "Source change detected; restarting the process." in caplog.text
