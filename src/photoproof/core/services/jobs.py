"""Durable job queue backed by SQLite.

Provides at-least-once execution of analysis jobs with lease-based worker
fencing and idempotent submission. Jobs are stored durably so they survive
application restarts; WebKit submits jobs and workers claim them.

No Redis, Celery or message broker: SQLite WAL mode supports one writer and
many readers, which is sufficient at this scale.
"""

from __future__ import annotations

import datetime
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
        """Submit an analysis job and return its ``job_id``.

        Idempotent: submitting the same request with the same client token
        returns the existing job_id instead of creating a duplicate.
        """
        payload = request.model_dump_json()
        idempotency_key = self._idempotency_key(payload, client_token)

        existing = self._db._conn.execute(
            "SELECT job_id FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            return str(existing[0])

        job_id = uuid.uuid4().hex
        now = datetime.datetime.now(datetime.UTC).isoformat()

        self._db._write(
            "INSERT INTO jobs (job_id, state, payload, attempts, max_attempts, "
            "lease_until, worker_id, idempotency_key, created_at, "
            "updated_at, error_message) "
            "VALUES (?, 'queued', ?, 0, 1, NULL, NULL, ?, ?, ?, NULL)",
            (job_id, payload, idempotency_key, now, now),
        )
        return job_id

    # ------------------------------------------------------------------ claim

    def claim(self, worker_id: str) -> Job | None:
        """Atomically claim the oldest queued job.

        Uses ``BEGIN IMMEDIATE`` for an immediate write lock so concurrent
        claimers cannot both select the same job. Returns the :class:`Job` or
        ``None`` if no queued jobs are available.
        """
        row = self._db.claim_job(worker_id, self._settings.lease_seconds)
        if row is None:
            return None
        return self._row_to_job(row)

    # ------------------------------------------------------------------ complete/fail

    def complete(self, job_id: str, result: Any = None) -> None:
        """Mark a job as succeeded."""
        self._db._write(
            "UPDATE jobs SET state = 'succeeded', updated_at = datetime('now') "
            "WHERE job_id = ? AND state = 'running'",
            (job_id,),
        )

    def fail(self, job_id: str, error_message: str) -> None:
        """Mark a job as failed.

        If attempts remain, re-queues the job. Otherwise marks it failed.
        """
        row = self._db._conn.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise StorageError(f"job {job_id} not found")
        attempts = int(row[0]) + 1
        max_attempts = int(row[1])

        if attempts < max_attempts:
            self._db._write(
                "UPDATE jobs SET state = 'queued', worker_id = NULL, "
                "lease_until = NULL, attempts = ?, updated_at = datetime('now') "
                "WHERE job_id = ? AND state = 'running'",
                (attempts, job_id),
            )
        else:
            self._db._write(
                "UPDATE jobs SET state = 'failed', error_message = ?, "
                "updated_at = datetime('now') WHERE job_id = ? AND state = 'running'",
                (error_message, job_id),
            )

    # ------------------------------------------------------------------ stale leases

    def requeue_stale(self) -> list[str]:
        """Re-queue stale leases or fail them after ``max_attempts``.

        Returns the list of job_ids that were re-queued or failed.
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        stale = self._db._conn.execute(
            "SELECT job_id, attempts, max_attempts FROM jobs "
            "WHERE state = 'running' AND lease_until < ?",
            (now,),
        ).fetchall()

        requeued: list[str] = []
        for row in stale:
            job_id = str(row[0])
            attempts = int(row[1]) + 1
            max_attempts = int(row[2])
            if attempts < max_attempts:
                self._db._write(
                    "UPDATE jobs SET state = 'queued', worker_id = NULL, "
                    "lease_until = NULL, attempts = ?, updated_at = datetime('now') "
                    "WHERE job_id = ? AND state = 'running'",
                    (attempts, job_id),
                )
                requeued.append(job_id)
            else:
                self._db._write(
                    "UPDATE jobs SET state = 'failed', "
                    "error_message = 'lease expired after max attempts', "
                    "updated_at = datetime('now') WHERE job_id = ? AND state = 'running'",
                    (job_id,),
                )
                requeued.append(job_id)
        return requeued

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
