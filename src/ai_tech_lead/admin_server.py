"""Small local admin screen for editing coding-agent settings."""

from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from ai_tech_lead.app_settings import (
    CodingAgentSettings,
    load_settings,
    parse_settings,
    save_settings,
)
from ai_tech_lead.config import SETTINGS_PATH


DEFAULT_ADMIN_HOST = "127.0.0.1"
DEFAULT_ADMIN_PORT = 8765


def run_admin_server(
    host: str = DEFAULT_ADMIN_HOST,
    port: int = DEFAULT_ADMIN_PORT,
    settings_path: Path = SETTINGS_PATH,
) -> None:
    """Start the local settings admin screen."""

    handler_class = _build_handler(settings_path)
    server = ThreadingHTTPServer((host, port), handler_class)
    print(f"Admin screen running at http://{host}:{port}/")
    server.serve_forever()


def _build_handler(settings_path: Path) -> type[BaseHTTPRequestHandler]:
    class AdminRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404, "Not found")
                return

            try:
                settings = load_settings(settings_path)
                body = _render_form(settings=settings, message="")
            except (FileNotFoundError, ValueError) as error:
                body = _render_error(error)

            self._send_html(body)

        def do_POST(self) -> None:
            if self.path != "/":
                self.send_error(404, "Not found")
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            form_data = parse_qs(
                self.rfile.read(content_length).decode("utf-8"),
                keep_blank_values=True,
            )

            try:
                settings = _settings_from_form(form_data)
                save_settings(settings, settings_path)
                body = _render_form(settings=settings, message="Settings saved.")
            except ValueError as error:
                current_settings = load_settings(settings_path)
                body = _render_form(settings=current_settings, message=str(error))

            self._send_html(body)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_html(self, body: str) -> None:
            encoded_body = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded_body)))
            self.end_headers()
            self.wfile.write(encoded_body)

    return AdminRequestHandler


def _settings_from_form(form_data: dict[str, list[str]]) -> CodingAgentSettings:
    raw_settings = {
        "max_runtime_minutes": _form_int(form_data, "max_runtime_minutes"),
        "allowed_directories": _form_lines(form_data, "allowed_directories"),
        "watched_directories": _form_lines(form_data, "watched_directories"),
        "risk_terms": _form_lines(form_data, "risk_terms"),
        "brief_constraints": _form_lines(form_data, "brief_constraints"),
        "acceptance_criteria": _form_lines(form_data, "acceptance_criteria"),
        "risk_notes": _form_lines(form_data, "risk_notes"),
        "prompts": {
            "execution_brief_template": _form_text(form_data, "execution_brief_template"),
            "agent_instruction_template": _form_text(form_data, "agent_instruction_template"),
        },
    }
    return parse_settings(raw_settings)


def _form_text(form_data: dict[str, list[str]], key: str) -> str:
    return form_data.get(key, [""])[0].strip()


def _form_int(form_data: dict[str, list[str]], key: str) -> int:
    value = _form_text(form_data, key)
    if not value.isdigit():
        raise ValueError(f"'{key}' must be a positive integer.")
    return int(value)


def _form_lines(form_data: dict[str, list[str]], key: str) -> list[str]:
    return [line.strip() for line in _form_text(form_data, key).splitlines() if line.strip()]


def _render_form(settings: CodingAgentSettings, message: str) -> str:
    status = f'<p class="status">{escape(message)}</p>' if message else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Tech Lead Admin</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Arial, Helvetica, sans-serif;
      line-height: 1.4;
      color: #202124;
      background: #f6f7f9;
    }}
    body {{
      margin: 0;
      padding: 28px;
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
    }}
    h1 {{
      margin: 0 0 18px;
      font-size: 30px;
    }}
    form {{
      display: grid;
      gap: 16px;
    }}
    fieldset {{
      border: 1px solid #d6dae0;
      border-radius: 8px;
      padding: 16px;
      background: #ffffff;
    }}
    legend {{
      font-weight: 700;
      padding: 0 6px;
    }}
    label {{
      display: grid;
      gap: 6px;
      margin: 12px 0;
      font-weight: 700;
    }}
    input, textarea {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #c6ccd4;
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
    }}
    textarea {{
      min-height: 110px;
      resize: vertical;
      font-family: Consolas, "Liberation Mono", monospace;
    }}
    .prompt {{
      min-height: 220px;
    }}
    button {{
      width: fit-content;
      border: 0;
      border-radius: 6px;
      padding: 10px 14px;
      font: inherit;
      font-weight: 700;
      color: #ffffff;
      background: #11695e;
      cursor: pointer;
    }}
    .status {{
      margin: 0 0 14px;
      color: #7a3f00;
      font-weight: 700;
    }}
  </style>
</head>
<body>
<main>
  <h1>AI Tech Lead Admin</h1>
  {status}
  <form method="post">
    <fieldset>
      <legend>Runtime</legend>
      {_input("Max runtime minutes", "max_runtime_minutes", str(settings.max_runtime_minutes))}
    </fieldset>
    <fieldset>
      <legend>Directories</legend>
      {_textarea("Allowed directories", "allowed_directories", settings.allowed_directories)}
      {_textarea("Watched directories", "watched_directories", settings.watched_directories)}
    </fieldset>
    <fieldset>
      <legend>Risk</legend>
      {_textarea("Risk terms", "risk_terms", settings.risk_terms)}
    </fieldset>
    <fieldset>
      <legend>Brief Content</legend>
      {_textarea("Brief constraints", "brief_constraints", settings.brief_constraints)}
      {_textarea("Acceptance criteria", "acceptance_criteria", settings.acceptance_criteria)}
      {_textarea("Risk notes", "risk_notes", settings.risk_notes)}
    </fieldset>
    <fieldset>
      <legend>Prompts</legend>
      {_textarea_value("Execution brief template", "execution_brief_template", settings.prompts["execution_brief_template"], css_class="prompt")}
      {_textarea_value("Agent instruction template", "agent_instruction_template", settings.prompts["agent_instruction_template"], css_class="prompt")}
    </fieldset>
    <button type="submit">Save settings</button>
  </form>
</main>
</body>
</html>"""


def _render_error(error: Exception) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>AI Tech Lead Admin</title></head>
<body>
  <h1>AI Tech Lead Admin</h1>
  <p>{escape(str(error))}</p>
</body>
</html>"""


def _input(label: str, name: str, value: str) -> str:
    return f'<label>{escape(label)}<input name="{escape(name)}" value="{escape(value)}"></label>'


def _textarea(label: str, name: str, values: list[str]) -> str:
    return _textarea_value(label, name, "\n".join(values))


def _textarea_value(label: str, name: str, value: str, css_class: str = "") -> str:
    class_attribute = f' class="{escape(css_class)}"' if css_class else ""
    return (
        f'<label>{escape(label)}'
        f'<textarea name="{escape(name)}"{class_attribute}>{escape(value)}</textarea>'
        "</label>"
    )
