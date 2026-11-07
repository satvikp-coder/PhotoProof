"""Worker manager for running multiple copy-move analysis workers.

Manages a pool of worker processes that claim jobs from the job queue.
Provides health monitoring, automatic restart of crashed workers with
exponential backoff, and graceful shutdown.

The manager runs worker processes that each claim and run jobs
independently via the job queue.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import signal
import time
from dataclasses import dataclass, field

from photoproof.core.services.worker import Worker


@dataclass
class WorkerInfo:
    """Track a worker process and its health."""

    worker_id: str
    process: multiprocessing.Process
    last_heartbeat: float
    restart_count: int = 0
    start_time: float = field(default_factory=lambda: __import__("time").time())


class WorkerManager:
    """Manage worker processes that run copy-move analysis jobs.

    Starts N worker processes, monitors their heartbeats, restarts crashed
    workers with exponential backoff, and shuts down gracefully.

    The manager runs worker processes that each claim and run jobs
    independently via the job queue.
    """

    def __init__(
        self,
        app_root: str,
        worker_count: int = 1,
        lease_seconds: int = 300,
        heartbeat_timeout: int = 60,
        restart_backoff_max: int = 300,
    ) -> None:
        self.app_root = app_root
        self.worker_count = worker_count
        self.lease_seconds = lease_seconds
        self.heartbeat_timeout = heartbeat_timeout
        self.restart_backoff_max = restart_backoff_max

        self._workers: dict[str, WorkerInfo] = {}
        self._running = False

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame: object) -> None:
        """Handle shutdown signals gracefully."""
        self._running = False

    def _start_worker(self, worker_id: str) -> None:
        """Start a single worker process."""
        process = multiprocessing.Process(
            target=_worker_process_main,
            args=(self.app_root, worker_id, self.lease_seconds),
            name=f"worker-{worker_id}",
        )
        process.start()

        self._workers[worker_id] = WorkerInfo(
            worker_id=worker_id,
            process=process,
            last_heartbeat=time.time(),
        )

    def _restart_worker(self, worker_id: str) -> None:
        """Restart a crashed worker with exponential backoff."""
        info = self._workers.get(worker_id)
        if info is None:
            return

        info.restart_count += 1
        backoff = min(self.restart_backoff_max, 2**info.restart_count)
        logger = logging.getLogger(__name__)
        logger.warning(
            "Worker %s crashed (attempt %d), restarting in %ds",
            worker_id,
            info.restart_count,
            backoff,
        )

        time.sleep(backoff)
        self._start_worker(worker_id)

    def _check_workers(self) -> None:
        """Check worker health and restart crashed workers."""
        for _worker_id, info in list(self._workers.items()):
            if not info.process.is_alive():
                logging.getLogger(__name__).warning("Worker %s has crashed", _worker_id)
                self._restart_worker(_worker_id)

    def run_forever(self, app_root: str) -> None:
        """Start workers and monitor them until shutdown signal."""
        self._running = True

        # Start initial workers
        for i in range(self.worker_count):
            worker_id = f"worker-{i}"
            self._start_worker(worker_id)

        logger = logging.getLogger(__name__)
        logger.info("Worker manager started with %d workers", self.worker_count)

        try:
            while self._running:
                self._check_workers()
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shut down all workers."""
        self._running = False
        for _worker_id, info in self._workers.items():
            if info.process.is_alive():
                info.process.terminate()
                info.process.join(timeout=5)
                if info.process.is_alive():
                    info.process.kill()
        self._workers.clear()


def main() -> None:
    """Entry point for running the worker manager as a standalone process."""
    parser = argparse.ArgumentParser(description="PhotoProof worker manager")
    parser.add_argument("--app-root", required=True, help="Path to PhotoProof app root")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    parser.add_argument(
        "--lease-seconds", type=int, default=300, help="Job lease duration in seconds"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    manager = WorkerManager(
        app_root=args.app_root,
        worker_count=args.workers,
        lease_seconds=args.lease_seconds,
    )
    manager.run_forever(args.app_root)


def _worker_process_main(
    app_root: str,
    worker_id: str,
    lease_seconds: int,
) -> None:
    """Entry point for a worker subprocess.

    Runs the Worker.run_forever loop in a separate process.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    worker = Worker(
        app_root=app_root,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    worker.run_forever(app_root)
