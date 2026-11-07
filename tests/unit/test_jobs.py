"""Unit tests for the durable job queue (photoproof.core.services.jobs)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.core.config.validator import JobsSettings
from photoproof.core.database.store import Database
from photoproof.core.services.jobs import FAILED, RUNNING, SUCCEEDED, JobQueue

SHA256_PLACEHOLDER = "a" * 64


def _make_request() -> AnalysisRequest:
    return AnalysisRequest(
        target=InputDescriptor(role="target", sha256=SHA256_PLACEHOLDER, size=1),
        profile="default",
        use_cache=True,
    )


def _open_db(tmp_path: Path) -> Database:
    return Database.open(tmp_path / "jobs.db")


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _json_dumps(obj) -> str:
    return json.dumps(obj)


def test_job_submission_creates_queued_job(tmp_path: Path) -> None:
    """Submitting a request creates a queued job with correct fields."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=300, max_attempts=1))
        request = _make_request()
        job_id = queue.submit(request, "client-1")

        assert job_id is not None
        assert len(job_id) > 0

        job = queue.claim("worker-1")
        assert job is not None
        assert job.job_id == job_id
        assert job.state == RUNNING  # claim transitions to running
        assert job.attempts == 1  # claim increments attempts
        assert job.max_attempts == 1
        assert job.worker_id == "worker-1"
        assert job.lease_until is not None
        assert job.request.target.sha256 == SHA256_PLACEHOLDER

        queue.complete(job_id)
        job2 = queue.claim("worker-2")
        assert job2 is None  # no more queued jobs
    finally:
        db.close()


def test_idempotent_submission_returns_same_job_id(tmp_path: Path) -> None:
    """Same request + client token returns the same job_id."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=300, max_attempts=1))
        request = _make_request()

        job_id_1 = queue.submit(request, "client-1")
        job_id_2 = queue.submit(request, "client-1")
        assert job_id_1 == job_id_2

        # Different client token creates a different job
        job_id_3 = queue.submit(request, "client-2")
        assert job_id_3 != job_id_1
    finally:
        db.close()


def test_two_claims_do_not_return_same_job(tmp_path: Path) -> None:
    """Two sequential claims never return the same job while running."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=300, max_attempts=1))
        request = _make_request()
        job_id = queue.submit(request, "client-1")

        job1 = queue.claim("worker-1")
        assert job1 is not None
        assert job1.job_id == job_id

        # Same job should not be claimable again while running
        job2 = queue.claim("worker-2")
        assert job2 is None
    finally:
        db.close()


def test_complete_marks_job_succeeded(tmp_path: Path) -> None:
    """Complete transitions a running job to succeeded."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=300, max_attempts=1))
        request = _make_request()
        job_id = queue.submit(request, "client-1")

        job = queue.claim("worker-1")
        assert job is not None
        assert job.state == RUNNING

        queue.complete(job_id)

        # Job should no longer be claimable
        job2 = queue.claim("worker-2")
        assert job2 is None

        # Verify via direct DB query
        row = db._conn.execute("SELECT state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        assert row is not None
        assert str(row[0]) == SUCCEEDED
    finally:
        db.close()


def test_fail_marks_failed_after_max_attempts(tmp_path: Path) -> None:
    """Fail with default max_attempts=1 marks the job as failed."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=300, max_attempts=1))
        request = _make_request()
        job_id = queue.submit(request, "client-1")

        job = queue.claim("worker-1")
        assert job is not None
        # Default max_attempts is 1, so this fail should mark as failed
        queue.fail(job_id, "permanent error")

        # Should not be claimable again
        job2 = queue.claim("worker-2")
        assert job2 is None

        # Verify via DB
        row = db._conn.execute(
            "SELECT state, error_message FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        assert row is not None
        assert str(row[0]) == FAILED
        assert str(row[1]) == "permanent error"
    finally:
        db.close()


def test_fail_requeues_when_attempts_remain(tmp_path: Path) -> None:
    """Fail with attempts < max_attempts re-queues the job."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=300, max_attempts=1))

        # Insert a job directly with max_attempts=3 so fail can re-queue
        job_id = "manual-job"
        payload = _json_dumps(
            {
                "target": {"role": "target", "sha256": SHA256_PLACEHOLDER, "size": 1},
                "profile": "default",
                "use_cache": True,
            }
        )
        db._conn.execute(
            "INSERT INTO jobs (job_id, state, payload, attempts, max_attempts, "
            "lease_until, worker_id, idempotency_key, created_at, updated_at, error_message) "
            "VALUES (?, 'running', ?, 0, 3, NULL, NULL, ?, ?, ?, NULL)",
            (job_id, payload, "manual-key", _now(), _now()),
        )

        # Fail with attempts < max_attempts (0 < 3) should re-queue
        queue.fail(job_id, "transient error")

        # Should be claimable again (re-queued)
        job2 = queue.claim("worker-2")
        assert job2 is not None
        assert job2.job_id == job_id
        assert job2.attempts == 2  # fail incremented to 1, claim incremented to 2
    finally:
        db.close()


def test_stale_lease_requeued(tmp_path: Path) -> None:
    """A stale lease (expired lease_until) is re-queued."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=1, max_attempts=1))
        # Insert a job directly with max_attempts=3 so stale lease can re-queue
        job_id = "test-stale-job"
        now = _now()
        payload = _json_dumps(
            {
                "target": {"role": "target", "sha256": SHA256_PLACEHOLDER, "size": 1},
                "profile": "default",
                "use_cache": True,
            }
        )
        db._conn.execute(
            "INSERT INTO jobs (job_id, state, payload, attempts, max_attempts, "
            "lease_until, worker_id, idempotency_key, created_at, updated_at, error_message) "
            "VALUES (?, 'running', ?, 1, 3, ?, 'worker-1', ?, ?, ?, NULL)",
            (
                job_id,
                payload,
                _now(),
                "stale-key",
                now,
                now,
            ),
        )
        # Manually expire the lease
        db._conn.execute(
            "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job_id),
        )

        requeued = queue.requeue_stale()
        assert job_id in requeued

        # Should be claimable again
        job2 = queue.claim("worker-2")
        assert job2 is not None
        assert job2.job_id == job_id
    finally:
        db.close()


def test_stale_lease_failed_after_max_attempts(tmp_path: Path) -> None:
    """A stale lease with no remaining attempts is marked failed."""
    db = _open_db(tmp_path)
    try:
        queue = JobQueue(db, JobsSettings(lease_seconds=1, max_attempts=1))
        request = _make_request()
        job_id = queue.submit(request, "client-1")

        job = queue.claim("worker-1")
        assert job is not None
        assert job.max_attempts == 1

        # Manually expire the lease
        db._conn.execute(
            "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job_id),
        )

        requeued = queue.requeue_stale()
        assert job_id in requeued

        # Should not be claimable again
        job2 = queue.claim("worker-2")
        assert job2 is None

        # Verify via DB
        row = db._conn.execute("SELECT state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        assert row is not None
        assert str(row[0]) == FAILED
    finally:
        db.close()


def test_queue_survives_restart(tmp_path: Path) -> None:
    """Queue survives application restart - data persists in SQLite."""
    db_path = tmp_path / "jobs.db"

    # First app instance: submit and claim a job, then expire its lease
    db1 = Database.open(db_path)
    try:
        queue1 = JobQueue(db1, JobsSettings(lease_seconds=300, max_attempts=3))
        request = _make_request()
        job_id = queue1.submit(request, "client-1")
        job = queue1.claim("worker-1")
        assert job is not None
        assert job.job_id == job_id
        assert job.state == RUNNING
        # Expire the lease so the job can be recovered after restart
        db1._conn.execute(
            "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job_id),
        )
        db1._conn.commit()
    finally:
        db1.close()

    # Second app instance: reopen same database, re-queue stale lease,
    # job should be claimable again
    db2 = Database.open(db_path)
    try:
        queue2 = JobQueue(db2, JobsSettings(lease_seconds=300, max_attempts=3))
        requeued = queue2.requeue_stale()
        assert job_id in requeued
        job2 = queue2.claim("worker-2")
        assert job2 is not None
        assert job2.job_id == job_id
        assert job2.state == RUNNING
        assert job2.attempts == 3  # submit(0) + claim(1) + requeue(2) + claim(3)
    finally:
        db2.close()
