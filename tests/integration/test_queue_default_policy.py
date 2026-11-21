"""Shipped configuration must enforce explicit opt-in for forensic retries."""

from pathlib import Path

from helpers import update_settings

from photoproof.contracts.analysis import AnalysisRequest, InputDescriptor
from photoproof.core.bootstrap import bootstrap


def _request() -> AnalysisRequest:
    return AnalysisRequest(target=InputDescriptor(role="target", sha256="a" * 64, size=1))


def test_shipped_settings_fail_after_first_claim(app_root: Path) -> None:
    app = bootstrap(app_root, environ={})
    try:
        app.storage.ensure_layout()
        app.start()
        assert app.settings.jobs.max_attempts == 1
        queue = app.job_queue()
        job_id = queue.submit(_request(), "default-one-attempt")
        claim = queue.claim("worker-default")
        assert claim is not None and claim.max_attempts == claim.attempts == 1
        queue.fail(job_id, "synthetic execution failure", claim=claim)
        result = queue.get(job_id)
        assert result is not None and result.state == "failed" and result.attempts == 1
        assert queue.claim("worker-other") is None
    finally:
        app.stop()


def test_explicit_retry_budget_is_stored_with_job(app_root: Path) -> None:
    update_settings(app_root, jobs={"max_attempts": 2})
    app = bootstrap(app_root, environ={})
    try:
        app.storage.ensure_layout()
        app.start()
        queue = app.job_queue()
        job_id = queue.submit(_request(), "explicit-two-attempts")
        first = queue.claim("worker-first")
        assert first is not None and first.max_attempts == 2
        queue.fail(job_id, "first synthetic failure", claim=first)
        second = queue.claim("worker-second")
        assert second is not None and second.job_id == job_id and second.attempts == 2
        queue.fail(job_id, "second synthetic failure", claim=second)
        result = queue.get(job_id)
        assert result is not None and result.state == "failed" and result.attempts == 2
        assert queue.claim("worker-third") is None
    finally:
        app.stop()
