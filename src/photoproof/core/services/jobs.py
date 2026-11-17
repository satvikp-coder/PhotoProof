"""Durable job queue backed by SQLite.

Provides at-least-once execution of analysis jobs with lease-based worker
fencing and idempotent submission. Jobs are stored durably so they survive
application restarts; WebKit submits jobs and workers claim them.

No Redis, Celery or message broker: SQLite WAL mode supports one writer and
many readers, which is sufficient at this scale.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.exceptions import StorageError
from photoproof.core.config.validator import JobsSettings
from photoproof.core.database.store import Database

#: Job states
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

_TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELLED})


@dataclass(frozen=True)
class Job:
    """A durable analysis job.

    The ``request`` is the validated ``AnalysisRequest`` (descriptors only,
    never image bytes). ``lease_until`` and ``worker_id`` are populated only
    while the job is running.
    """

    job_id: str
    state: str
    request: AnalysisRequest
    attempts: int
    max_attempts: int
    lease_until: str | None
    worker_id: str | None
    idempotency_key: str
    created_at: str
    updated_at: str
    error_message: str | None


class JobQueue:
    """Durable job queue stored in the SQLite database.

    Jobs are claimed atomically with leases. Crashed workers leave stale
    leases that are re-queued (up to ``max_attempts``) or failed. Submission
    is idempotent: the same request plus client token yields the same
    ``job_id``.
    """

    def __init__(self, db: Database, settings: JobsSettings) -> None:
        self._db = db
        self._settings = settings

    # ------------------------------------------------------------------ submit

    def submit(self, request: AnalysisRequest, client_token: str) -> str:
        """Atomically submit a descriptor-only request with client idempotency."""
        payload = request.model_dump_json()
        return self._db.submit_job(
            job_id=uuid.uuid4().hex,
            payload=payload,
            max_attempts=self._settings.max_attempts,
            idempotency_key=self._idempotency_key(payload, client_token),
        )

    def get(self, job_id: str) -> Job | None:
        """Read a job without claiming it."""
        row = self._db.get_job(job_id)
        return self._row_to_job(row) if row is not None else None

    def claim(self, worker_id: str) -> Job | None:
        """Atomically claim a queued job and return its fenced ownership token."""
        row = self._db.claim_job(worker_id, self._settings.lease_seconds)
        if row is None:
            return None
        return self._row_to_job(row)

    def _owned_claim(self, job_id: str, claim: Job) -> Job:
        token = claim
        if token.job_id != job_id or not token.worker_id:
            raise StorageError(f"job {job_id}: no owned claim; claim it before changing its state")
        return token

    def complete(self, job_id: str, result: Any = None, *, claim: Job) -> None:
        """Complete an owned live claim; repeating its completion is idempotent.

        Pass the original Job returned by claim, never a later lookup of the job.
        The legacy result parameter is retained; run linkage is handled by workers.
        """
        token = self._owned_claim(job_id, claim)
        if not self._db.transition_claimed_job(
            job_id, worker_id=str(token.worker_id), attempt=token.attempts
        ):
            raise StorageError(
                f"job {job_id}: claim expired or replaced; do not publish completion"
            )

    def fail(self, job_id: str, error_message: str, *, claim: Job) -> None:
        """Fail an owned live claim, preserving attempts for the next actual claim."""
        token = self._owned_claim(job_id, claim)
        if not self._db.transition_claimed_job(
            job_id,
            worker_id=str(token.worker_id),
            attempt=token.attempts,
            error_message=error_message,
        ):
            raise StorageError(f"job {job_id}: claim expired or replaced; do not publish failure")

    def renew(self, job_id: str, *, claim: Job) -> bool:
        """Renew a still-live owned claim; return False when it has expired/replaced."""
        token = self._owned_claim(job_id, claim)
        return self._db.renew_job_claim(
            job_id,
            worker_id=str(token.worker_id),
            attempt=token.attempts,
            lease_seconds=self._settings.lease_seconds,
        )

    def requeue_stale(self) -> list[str]:
        """Atomically recover expired claims according to the stored attempt budget."""
        return self._db.recover_expired_jobs()

    # ------------------------------------------------------------------ internal

    def _idempotency_key(self, payload: str, client_token: str) -> str:
        """Compute the idempotency key from request payload and client token."""
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{digest}:{client_token}"

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        """Convert a database row to a :class:`Job`."""
        request = AnalysisRequest.model_validate_json(row["payload"])
        return Job(
            job_id=str(row["job_id"]),
            state=str(row["state"]),
            request=request,
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            lease_until=(str(row["lease_until"]) if row["lease_until"] else None),
            worker_id=(str(row["worker_id"]) if row["worker_id"] else None),
            idempotency_key=str(row["idempotency_key"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            error_message=(str(row["error_message"]) if row["error_message"] else None),
        )
