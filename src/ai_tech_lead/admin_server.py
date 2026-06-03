"""Local admin API and static HTML server for coding-agent settings."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ai_tech_lead.app_settings import (
    load_settings,
    parse_settings,
    save_settings,
    settings_to_dict,
)
from ai_tech_lead.config import SETTINGS_PATH


DEFAULT_ADMIN_HOST = "127.0.0.1"
DEFAULT_ADMIN_PORT = 8766
ADMIN_ASSET_DIR = Path(__file__).resolve().parent / "admin"
ADMIN_HTML_PATH = ADMIN_ASSET_DIR / "admin.html"
ADMIN_CSS_PATH = ADMIN_ASSET_DIR / "admin.css"
ADMIN_JS_PATH = ADMIN_ASSET_DIR / "admin.js"


def run_admin_server(
    host: str = DEFAULT_ADMIN_HOST,
    port: int = DEFAULT_ADMIN_PORT,
    settings_path: Path = SETTINGS_PATH,
    admin_html_path: Path = ADMIN_HTML_PATH,
    admin_css_path: Path = ADMIN_CSS_PATH,
    admin_js_path: Path = ADMIN_JS_PATH,
) -> None:
    """Start the local settings admin screen and JSON API."""

    handler_class = _build_handler(
        settings_path=settings_path,
        admin_html_path=admin_html_path,
        admin_css_path=admin_css_path,
        admin_js_path=admin_js_path,
    )
    server = ThreadingHTTPServer((host, port), handler_class)
    print(f"Admin screen running at http://{host}:{port}/")
    print(f"Settings API running at http://{host}:{port}/api/settings")
    server.serve_forever()


def _build_handler(
    settings_path: Path,
    admin_html_path: Path,
    admin_css_path: Path,
    admin_js_path: Path,
) -> type[BaseHTTPRequestHandler]:
    class AdminRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                self._send_static_file(
                    path=admin_html_path,
                    content_type="text/html; charset=utf-8",
                )
                return

            if self.path == "/admin.css":
                self._send_static_file(
                    path=admin_css_path,
                    content_type="text/css; charset=utf-8",
                )
                return

            if self.path == "/admin.js":
                self._send_static_file(
                    path=admin_js_path,
                    content_type="text/javascript; charset=utf-8",
                )
                return

            if self.path == "/api/settings":
                self._send_settings()
                return

            self.send_error(404, "Not found")

        def do_PUT(self) -> None:
            if self.path != "/api/settings":
                self.send_error(404, "Not found")
                return

            self._save_settings_from_json()

        def do_POST(self) -> None:
            if self.path != "/api/settings":
                self.send_error(404, "Not found")
                return

            self._save_settings_from_json()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_static_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                self._send_json(
                    {"error": f"Admin asset file not found: {path}"},
                    status=500,
                )
                return

            self._send_bytes(
                path.read_bytes(),
                content_type=content_type,
            )

        def _send_settings(self) -> None:
            try:
                settings = load_settings(settings_path)
                self._send_json(settings_to_dict(settings))
            except (FileNotFoundError, ValueError) as error:
                self._send_json({"error": str(error)}, status=500)

        def _save_settings_from_json(self) -> None:
            try:
                raw_settings = self._read_json_body()
                settings = parse_settings(raw_settings)
                save_settings(settings, settings_path)
                self._send_json(settings_to_dict(settings))
            except ValueError as error:
                self._send_json({"error": str(error)}, status=400)

        def _read_json_body(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8")

            try:
                raw_settings = json.loads(body)
            except json.JSONDecodeError as error:
                raise ValueError(f"Request body must be valid JSON: {error}") from error

            if not isinstance(raw_settings, dict):
                raise ValueError("Request body must be a JSON object.")

            return raw_settings

        def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
            encoded_body = (json.dumps(data, indent=2) + "\n").encode("utf-8")
            self._send_bytes(
                encoded_body,
                content_type="application/json; charset=utf-8",
                status=status,
            )

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: int = 200,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return AdminRequestHandler
