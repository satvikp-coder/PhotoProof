"""A killed real process loses its lease and cannot orphan a queued analysis."""

import multiprocessing
import time
from pathlib import Path

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.core.config.validator import JobsSettings
from photoproof.core.database.store import Database
from photoproof.core.services.jobs import JobQueue


def _claim_then_wait(path, ready, stop):
    with Database.open(Path(path)) as db:
        queue = JobQueue(db, JobsSettings(lease_seconds=2, max_attempts=2))
        job = queue.claim("killed-worker")
        ready.send(job.job_id if job else None)
        ready.close()
        stop.wait(20)


def test_killed_claimant_is_recovered_without_double_counting(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    settings = JobsSettings(lease_seconds=2, max_attempts=2)
    with Database.open(path) as db:
        queue = JobQueue(db, settings)
        job_id = queue.submit(
            AnalysisRequest(target=InputDescriptor(role="target", sha256="a" * 64, size=1)),
            "crash-case",
        )
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(
            target=_claim_then_wait, args=(str(path), writer, context.Event())
        )
        process.start()
        writer.close()
        try:
            assert reader.poll(20), "child did not claim the test job"
            assert reader.recv() == job_id
            process.terminate()
            process.join(5)
            assert not process.is_alive()
            deadline = time.monotonic() + 10
            while not queue.requeue_stale():
                assert time.monotonic() < deadline, "expired claim was not recovered"
                time.sleep(0.05)
            replacement = queue.claim("replacement-worker")
            assert replacement is not None and replacement.job_id == job_id
            assert replacement.attempts == 2
            queue.complete(job_id, claim=replacement)
            assert queue.get(job_id).state == "succeeded"
        finally:
            reader.close()
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()
