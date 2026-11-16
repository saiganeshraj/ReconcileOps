"""Run inside the app container: python scripts/reset_demo.py."""

from reconcileops.storage.database import get_db_context
from reconcileops.storage.demo import reset_owned_demo

if __name__ == "__main__":
    with get_db_context() as db:
        print(reset_owned_demo(db))
