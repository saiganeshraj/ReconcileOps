"""Deterministic seed via the configured mock service, without hidden fallbacks."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from reconcileops.config import settings
from reconcileops.provider.client import ProviderClient
from reconcileops.storage.csv_importer import commit_import_batch, process_csv_preview
from reconcileops.storage.models import DemoOwnership, OrderModel, ProviderEventModel

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "orders_baseline.csv"


def own(db: Session, kind: str, ids) -> None:
    for entity_id in ids:
        if not db.get(DemoOwnership, (kind, entity_id)):
            db.add(DemoOwnership(entity_type=kind, entity_id=entity_id))
    db.flush()


def run_seed_baseline(db: Session) -> dict[str, int]:
    if not settings.DEMO_MODE:
        raise ValueError("Seeding requires DEMO_MODE=true.")
    batch = process_csv_preview(db, "orders_baseline.csv", FIXTURE_PATH.read_bytes())
    commit_import_batch(db, batch.id, actor="demo_seeder")
    own(db, "batch", [batch.id])
    own(
        db,
        "order",
        db.scalars(select(OrderModel.order_id).where(OrderModel.import_batch_id == batch.id)).all(),
    )
    db.commit()
    sync = ProviderClient().sync_all_events(db)
    own(db, "sync", [sync.id])
    own(
        db,
        "event",
        db.scalars(
            select(ProviderEventModel.event_id).where(ProviderEventModel.sync_run_id == sync.id)
        ).all(),
    )
    db.commit()
    return {
        "orders_imported": batch.accepted_rows,
        "events_synced": sync.records_imported + sync.records_duplicate,
    }
