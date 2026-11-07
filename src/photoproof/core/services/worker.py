"""Worker process for running copy-move analysis jobs.

A worker runs as a separate process, claims jobs from the job queue,
executes them through the controller, and updates job status.

Each worker runs in its own process and claims jobs from the job queue,
reads the job payload from the database, runs the analysis controller,
and updates job status.

The worker maintains a heartbeat via the job lease_until field so
the manager can detect crashes.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from pathlib import Path

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.core.bootstrap import Application, bootstrap
from photoproof.core.config.validator import JobsSettings
from photoproof.core.controller import AnalysisController
from photoproof.core.database.store import Database
from photoproof.core.services.jobs import JobQueue

logger = logging.getLogger(__name__)


class Worker:
    """Run copy-move analysis jobs from the queue.

    Each worker runs in its own process, claims jobs from the job queue,
    reads the job payload from the database, runs the analysis controller,
    and updates job status.

    The worker maintains a heartbeat via the job lease_until field so
    the manager can detect crashes.
    """

    def __init__(
        self,
        app_root: str,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> None:
        self.app_root = app_root
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._running = True
        self._current_job_id: str | None = None

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame: object) -> None:
        """Handle shutdown signals gracefully."""
        logger.info("Worker %s received signal %s, shutting down", self.worker_id, signum)
        self._running = False

    def _update_lease(self, db: Database, job_id: str) -> None:
        """Renew the lease on the current job to prevent it from being re-queued."""
        try:
            db._conn.execute(
                "UPDATE jobs SET lease_until = datetime('now', '+' || ? || ' seconds') "
                "WHERE job_id = ? AND state = 'running'",
                (str(self.lease_seconds), job_id),
            )
            db._conn.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001 - intentionally swallow lease update failures
            logger.debug("Failed to update lease for job %s: %s", job_id, exc)

    def _process_job(self, app: Application, job_id: str) -> bool:
        """Process a single job through the analysis controller.

        Returns True if the job completed successfully, False otherwise.
        """
        db = app.database()

        # Read the job payload from the database
        row = db._conn.execute("SELECT payload FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            logger.warning("Job %s not found in database", job_id)
            return False

        request = AnalysisRequest.model_validate_json(row[0])

        queue = JobQueue(db, JobsSettings(lease_seconds=self.lease_seconds, max_attempts=1))
        controller = AnalysisController(app)
        result = controller.analyze(request)

        if result.status.value in ("COMPLETED", "NOT_APPLICABLE"):
            queue.complete(job_id)
            logger.info("Job %s completed successfully", job_id)
            return True
        else:
            queue.fail(job_id, f"Analysis failed: {result.status.value}")
            logger.warning("Job %s failed with status %s", job_id, result.status.value)
            return False

    def run_once(self, app_root: str) -> bool:
        """Run one job from the queue.

        Returns True if a job was processed, False if no jobs were available.
        """
        app = bootstrap(Path(app_root), plugin_package="fixtures.mockplugins.demo", environ={})
        try:
            app.storage.ensure_layout()
            app.start()

            db = app.database()
            queue = JobQueue(db, JobsSettings(lease_seconds=self.lease_seconds, max_attempts=1))
            job = queue.claim(self.worker_id)

            if job is None:
                app.stop()
                return False

            self._current_job_id = job.job_id
            logger.info("Worker %s processing job %s", self.worker_id, job.job_id)

            try:
                success = self._process_job(app, job.job_id)
                return success
            finally:
                self._current_job_id = None
                app.stop()
        except Exception as e:
            logger.exception("Worker %s error processing job: %s", self.worker_id, e)
            if self._current_job_id is not None:
                try:
                    queue = JobQueue(
                        app.database(),
                        JobsSettings(lease_seconds=self.lease_seconds, max_attempts=1),
                    )
                    queue.fail(self._current_job_id, str(e))
                except Exception as exc:  # noqa: BLE001 - intentionally swallow queue failure
                    logger.debug("Failed to report job failure: %s", exc)
            return False
        finally:
            app.stop()

    def run_forever(self, app_root: str) -> None:
        """Run the worker loop, claiming and processing jobs until stopped."""
        logger.info("Worker %s starting", self.worker_id)

        while self._running:
            try:
                self.run_once(app_root)
            except Exception as e:
                logger.exception("Worker %s error: %s", self.worker_id, e)

            # Small sleep between claims to avoid busy-spinning
            time.sleep(1)

        logger.info("Worker %s stopped", self.worker_id)

    def stop(self) -> None:
        """Request the worker to stop after finishing its current job."""
        self._running = False


def main() -> None:
    """Entry point for running the worker as a standalone process."""
    parser = argparse.ArgumentParser(description="PhotoProof worker process")
    parser.add_argument("--app-root", required=True, help="Path to PhotoProof app root")
    parser.add_argument("--worker-id", required=True, help="Unique worker ID")
    parser.add_argument("--lease-seconds", type=int, default=300, help="Lease duration in seconds")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    worker = Worker(
        app_root=args.app_root,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )
    worker.run_forever(args.app_root)


if __name__ == "__main__":
    main()
