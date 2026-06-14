from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from ai_tech_lead import admin_server
from ai_tech_lead.prompt_loader import load_prompt


def test_prompt_api_lists_and_saves_prompt_registry(tmp_path: Path) -> None:
    prompts_path = tmp_path / "prompts.json"
    prompts_path.write_text(
        json.dumps(
            {
                "prompts": [
                    {
                        "key": "alpha",
                        "prompt_class": "instruction",
                        "purpose": "Alpha purpose",
                        "used_by": ["alpha.py"],
                        "template": "Alpha one",
                    },
                    {
                        "key": "beta",
                        "prompt_class": "template",
                        "purpose": "Beta purpose",
                        "used_by": ["beta.py"],
                        "template": "Beta one",
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    handler_class = admin_server._build_handler(
        settings_path=settings_path,
        prompts_path=prompts_path,
        admin_html_path=tmp_path / "admin.html",
        admin_css_path=tmp_path / "admin.css",
        admin_js_path=tmp_path / "admin.js",
        admin_form_js_path=tmp_path / "admin_form.js",
        admin_config_js_path=tmp_path / "admin_config.js",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        with urlopen(f"{base_url}/api/prompts") as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert [item["key"] for item in payload["prompts"]] == ["alpha", "beta"]
        assert payload["prompts"][0]["template"] == "Alpha one"
        assert payload["prompts"][1]["purpose"] == "Beta purpose"

        updated_payload = {
            "prompts": [
                {
                    "key": "alpha",
                    "prompt_class": "instruction",
                    "purpose": "Alpha purpose",
                    "used_by": ["alpha.py"],
                    "template": "Alpha two",
                },
                {
                    "key": "beta",
                    "prompt_class": "template",
                    "purpose": "Beta purpose",
                    "used_by": ["beta.py"],
                    "template": "Beta two",
                },
            ]
        }
        request = Request(
            f"{base_url}/api/prompts",
            data=json.dumps(updated_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )

        with urlopen(request) as response:
            saved_payload = json.loads(response.read().decode("utf-8"))

        assert [item["template"] for item in saved_payload["prompts"]] == [
            "Alpha two",
            "Beta two",
        ]
        assert (prompts_path).read_text(encoding="utf-8").count("Alpha two") == 1
        assert load_prompt("alpha", path=prompts_path) == "Alpha two"
        assert load_prompt("beta", path=prompts_path) == "Beta two"
    finally:
        server.shutdown()
        thread.join(timeout=5)
