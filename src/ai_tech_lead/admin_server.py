"""Local admin API and static HTML server for coding-agent settings."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ai_tech_lead.app_settings import (
    load_settings,
    parse_settings,
    save_settings,
    settings_to_dict,
)
from ai_tech_lead.logging_setup import LOGGER_NAME

ADMIN_ASSET_DIR = Path(__file__).resolve().parent / "admin"
ADMIN_HTML_PATH = ADMIN_ASSET_DIR / "admin.html"
ADMIN_CSS_PATH = ADMIN_ASSET_DIR / "admin.css"
ADMIN_JS_PATH = ADMIN_ASSET_DIR / "admin.js"
ADMIN_FORM_JS_PATH = ADMIN_ASSET_DIR / "admin_form.js"
ADMIN_CONFIG_JS_PATH = ADMIN_ASSET_DIR / "admin_config.js"
ADMIN_ROOT_ROUTE = "/"
SETTINGS_API_ROUTE = "/api/settings"
ADMIN_CSS_ROUTE = "/admin.css"
ADMIN_JS_ROUTE = "/admin.js"
ADMIN_FORM_JS_ROUTE = "/admin_form.js"
ADMIN_CONFIG_JS_ROUTE = "/admin_config.js"

logger = logging.getLogger(LOGGER_NAME)


def run_admin_server(
    host: str,
    port: int,
    settings_path: Path,
    admin_html_path: Path = ADMIN_HTML_PATH,
    admin_css_path: Path = ADMIN_CSS_PATH,
    admin_js_path: Path = ADMIN_JS_PATH,
    admin_form_js_path: Path = ADMIN_FORM_JS_PATH,
    admin_config_js_path: Path = ADMIN_CONFIG_JS_PATH,
) -> None:
    """Start the local settings admin screen and JSON API."""

    handler_class = _build_handler(
        settings_path=settings_path,
        admin_html_path=admin_html_path,
        admin_css_path=admin_css_path,
        admin_js_path=admin_js_path,
        admin_form_js_path=admin_form_js_path,
        admin_config_js_path=admin_config_js_path,
    )
    server = ThreadingHTTPServer((host, port), handler_class)
    logger.info("Admin screen running at http://%s:%s%s", host, port, ADMIN_ROOT_ROUTE)
    logger.info("Settings API running at http://%s:%s%s", host, port, SETTINGS_API_ROUTE)
    server.serve_forever()


def _build_handler(
    settings_path: Path,
    admin_html_path: Path,
    admin_css_path: Path,
    admin_js_path: Path,
    admin_form_js_path: Path,
    admin_config_js_path: Path,
) -> type[BaseHTTPRequestHandler]:
    class AdminRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == ADMIN_ROOT_ROUTE:
                self._send_static_file(
                    path=admin_html_path,
                    content_type="text/html; charset=utf-8",
                )
                return

            if self.path == ADMIN_CSS_ROUTE:
                self._send_static_file(
                    path=admin_css_path,
                    content_type="text/css; charset=utf-8",
                )
                return

            if self.path == ADMIN_JS_ROUTE:
                self._send_static_file(
                    path=admin_js_path,
                    content_type="text/javascript; charset=utf-8",
                )
                return

            if self.path == ADMIN_FORM_JS_ROUTE:
                self._send_static_file(
                    path=admin_form_js_path,
                    content_type="text/javascript; charset=utf-8",
                )
                return

            if self.path == ADMIN_CONFIG_JS_ROUTE:
                self._send_static_file(
                    path=admin_config_js_path,
                    content_type="text/javascript; charset=utf-8",
                )
                return

            if self.path == SETTINGS_API_ROUTE:
                self._send_settings()
                return

            self.send_error(404, "Not found")

        def do_PUT(self) -> None:
            if self.path == SETTINGS_API_ROUTE:
                self._save_settings_from_json()
                return

            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            if self.path == SETTINGS_API_ROUTE:
                self._save_settings_from_json()
                return

            self.send_error(404, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("Admin HTTP: " + format, *args)

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
