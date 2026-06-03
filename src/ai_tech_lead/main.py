"""Command-line entry point for the local AI Technical Lead Assistant."""

from ai_tech_lead.brief_graph import run_sample_graph
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.storage import initialize_database


def main() -> None:
    """Initialize local storage and run the current sample workflow."""

    db_path = initialize_database()

    print("AI Technical Lead Assistant started")
    print(f"Project root detected: {PROJECT_ROOT}")
    print(f"SQLite ready: {db_path}")
    run_sample_graph()


if __name__ == "__main__":
    main()
