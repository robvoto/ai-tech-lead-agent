"""Project paths for the local AI Technical Lead Assistant."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"
DIAGRAMS_DIR = DOCS_DIR / "diagrams"
LOGS_DIR = PROJECT_ROOT / "logs"
SETTINGS_PATH = DATA_DIR / "coding_agent_settings.json"
DB_PATH = DATA_DIR / "ai_tech_lead.sqlite3"
GRAPH_DIAGRAM_PATH = DIAGRAMS_DIR / "graph_diagram.png"


def ensure_project_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
