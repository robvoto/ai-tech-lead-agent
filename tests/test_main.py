from __future__ import annotations

from dataclasses import replace

from ai_tech_lead.app_settings import parse_settings
import ai_tech_lead.main as main_module

from helpers import valid_settings_dict


def test_main_starts_admin_and_telegram_when_enabled(monkeypatch) -> None:
    started_threads: list[str] = []
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
    monkeypatch.setattr(main_module, "run_admin_server", lambda: None)
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(main_module, "_parse_args", lambda: type("Args", (), {"debug": False})())

    main_module.main()

    assert started_threads == ["admin-server", "telegram-operator"]


def test_main_starts_admin_only_when_telegram_disabled(monkeypatch) -> None:
    started_threads: list[str] = []
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
    monkeypatch.setattr(main_module, "run_admin_server", lambda: None)
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(main_module, "_parse_args", lambda: type("Args", (), {"debug": False})())

    main_module.main()

    assert started_threads == ["admin-server"]


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
    monkeypatch.setattr(main_module, "export_graph_diagrams", lambda *, settings: exported_settings.append(settings))
    monkeypatch.setattr(main_module, "run_admin_server", lambda: None)
    monkeypatch.setattr(main_module, "run_telegram_operator", lambda: None)
    monkeypatch.setattr(main_module, "_parse_args", lambda: type("Args", (), {"debug": True})())

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
