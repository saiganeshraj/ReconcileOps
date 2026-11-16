"""CLI script to seed deterministic baseline demo data."""

import sys
from pathlib import Path

# Add src to python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from reconcileops.scripts_seed import run_seed_baseline
from reconcileops.storage.database import get_db_context


def main():
    print("--- Seeding ReconcileOps Baseline Demo Data ---")
    with get_db_context() as session:
        result = run_seed_baseline(session)
        print(f"Successfully seeded: {result}")


if __name__ == "__main__":
    main()
