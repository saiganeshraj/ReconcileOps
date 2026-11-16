"""Finding review service with optimistic concurrency control and audit history."""

from sqlalchemy.orm import Session

from reconcileops.storage.models import AuditEvent, FindingModel, utc_now


class OptimisticLockError(Exception):
    pass


def resolve_finding(
    session: Session,
    finding_id: str,
    note: str,
    expected_version: int | None = None,
    actor: str = "operator",
) -> FindingModel:
    """Resolve an open or reopened finding with optimistic locking and mandatory note."""
    if not note or not note.strip():
        raise ValueError("A resolution note is required.")

    finding = session.get(FindingModel, finding_id)
    if not finding:
        raise ValueError(f"Finding '{finding_id}' not found.")

    if expected_version is not None and finding.version != expected_version:
        raise OptimisticLockError(
            f"Conflict: Finding '{finding_id}' has been modified by another operator "
            f"(expected version {expected_version}, current version {finding.version}). "
            "Please reload and review before submitting."
        )

    before_state = finding.status
    if before_state == "RESOLVED":
        raise ValueError("Finding is already resolved. Reopen it before another resolution.")
    finding.status = "RESOLVED"
    finding.resolution_note = note.strip()
    finding.version += 1
    finding.updated_at = utc_now()

    audit = AuditEvent(
        actor=actor,
        action="RESOLVE_FINDING",
        entity_type="finding",
        entity_id=finding.id,
        before_state=before_state,
        after_state="RESOLVED",
        note=note.strip(),
    )
    session.add(audit)
    session.flush()
    return finding


def reopen_finding(
    session: Session,
    finding_id: str,
    note: str,
    expected_version: int | None = None,
    actor: str = "operator",
) -> FindingModel:
    """Reopen a resolved finding with optimistic locking and mandatory note."""
    if not note or not note.strip():
        raise ValueError("A reopen note is required.")

    finding = session.get(FindingModel, finding_id)
    if not finding:
        raise ValueError(f"Finding '{finding_id}' not found.")

    if expected_version is not None and finding.version != expected_version:
        raise OptimisticLockError(
            f"Conflict: Finding '{finding_id}' has been modified by another operator "
            f"(expected version {expected_version}, current version {finding.version}). "
            "Please reload and review before submitting."
        )

    before_state = finding.status
    if before_state != "RESOLVED":
        raise ValueError("Only resolved findings can be reopened.")
    finding.status = "REOPENED"
    finding.resolution_note = note.strip()
    finding.version += 1
    finding.updated_at = utc_now()

    audit = AuditEvent(
        actor=actor,
        action="REOPEN_FINDING",
        entity_type="finding",
        entity_id=finding.id,
        before_state=before_state,
        after_state="REOPENED",
        note=note.strip(),
    )
    session.add(audit)
    session.flush()
    return finding
