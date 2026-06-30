"""Project paths for the local AI Technical Lead Assistant."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"
LOGS_DIR = PROJECT_ROOT / "logs"
SETTINGS_PATH = DATA_DIR / "coding_agent_settings.json"
DB_PATH = DATA_DIR / "ai_tech_lead.sqlite3"
CODING_AGENT_LOCK_FILE = PROJECT_ROOT / ".ai_tech_lead_agent_running"
GRAPH_DIAGRAM_PATH = DOCS_DIR / "graph_diagram.png"
TELEGRAM_AGENT_GRAPH_DIAGRAM_PATH = DOCS_DIR / "telegram_agent_graph.png"


def ensure_project_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
