from __future__ import annotations

import logging
from dataclasses import replace

import pytest
from helpers import valid_settings_dict

import ai_tech_lead.main as main_module
from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.config import PROJECT_ROOT


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
