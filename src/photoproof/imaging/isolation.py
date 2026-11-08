"""Isolated image decoding in a resource-limited child process.

``IsolatedDecoder`` wraps an in-process :class:`ImageDecoder` adapter and runs its
``decode`` method in a short-lived child process with CPU, memory and wall-clock
limits. A decoder crash, hang or memory blow-up affects only the worker; the parent
reports a typed error and continues.

When ``isolation`` is ``"in_process"`` (development only), decoding runs directly in
the calling process with no isolation -- use this only when the platform cannot
enforce child-process limits.
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import threading
from typing import Any, cast

from photoproof.contracts.exceptions import (
    DecoderCrashed,
    DecoderError,
    DecoderMemoryLimit,
    DecoderTimeout,
    InputRejectedError,
)
from photoproof.imaging.decoder import DecodedImage, DecodeLimits, ImageDecoder

try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None  # type: ignore[assignment]


def _apply_limits(limits_dict: dict[str, Any]) -> None:
    """Apply resource limits inside the child process before any decoding."""
    if resource is None:
        return
    # Memory ceiling: derived from the input size plus decoded pixel budget.
    memory_bytes = limits_dict.get("max_input_bytes", 0) + limits_dict.get(
        "max_decoded_bytes", 2 << 30
    )
    if memory_bytes > 0:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

    timeout_seconds = limits_dict.get("timeout_seconds", 30)
    if timeout_seconds > 0:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CPU, (int(timeout_seconds), int(timeout_seconds)))


def _child_entry(
    adapter_name: str,
    limits_dict: dict[str, Any],
    conn: Any,
) -> None:
    """Run in the child process: set limits, decode, send result back.

    The worker is started once and reused for multiple decode calls. Each request
    arrives on ``conn`` as ``(data_bytes,)``; each response is ``("ok",
    decoded_image)`` or ``("error", exception)``. A ``None`` request shuts down
    the worker.
    """
    try:
        # Import the adapter lazily inside the child.
        module_name, class_name = adapter_name.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        adapter_cls = getattr(module, class_name)
        adapter = adapter_cls()

        while True:
            msg = conn.recv()
            if msg is None:  # shutdown signal
                return
            if msg[0] == b"__ping__":
                conn.send(("ok", None))
                continue
            data, request_limits_dict = msg
            try:
                limits = limits_dict_to_limits(request_limits_dict)
                result = adapter.decode(data, source_sha256="", limits=limits)
                conn.send(("ok", result))
            except Exception as exc:  # noqa: BLE001 - child must report any failure
                conn.send(("error", exc))
    except Exception as exc:  # noqa: BLE001 - child startup failure
        with contextlib.suppress(Exception):
            conn.send(("error", exc))


def limits_dict_to_limits(d: dict[str, Any]) -> DecodeLimits:
    """Convert a limits dict to a DecodeLimits instance."""
    return DecodeLimits(
        max_input_bytes=d.get("max_input_bytes", 0),
        max_pixels=d.get("max_pixels", 0),
        max_frames=d.get("max_frames", 1),
        timeout_seconds=d.get("timeout_seconds", 30.0),
        max_decoded_bytes=d.get("max_decoded_bytes", 2 << 30),
    )


def _decode_in_process(
    adapter: ImageDecoder,
    data: bytes,
    source_sha256: str,
    limits: DecodeLimits,
) -> DecodedImage:
    """Decode in the current process (no isolation, development only)."""
    return adapter.decode(data, source_sha256=source_sha256, limits=limits)


class _IsolatedWorker:
    """A persistent child process that decodes images with resource limits.

    The worker is started once and reused for multiple decode calls. This avoids
    the overhead of spawning a new process (and re-importing heavy libraries like
    Pillow) for every decode, which was too slow on CI runners.
    """

    def __init__(
        self,
        adapter: ImageDecoder,
        isolation: str,
        startup_timeout: float = 30.0,
    ) -> None:
        self._adapter = adapter
        self._isolation = isolation
        self._startup_timeout = startup_timeout
        self._proc: Any = None
        self._conn: Any = None
        self._start_child()

    def _start_child(self) -> None:
        """Start the isolated child process and wait for it to be ready."""
        # Use fork context when available (faster than spawn, no re-import),
        # fall back to spawn on platforms without fork (Windows).
        try:
            ctx: Any = mp.get_context("fork")
        except (ValueError, AttributeError):
            ctx = mp.get_context("spawn")

        parent_conn, child_conn = ctx.Pipe()

        adapter_name = f"{self._adapter.__class__.__module__}.{self._adapter.__class__.__name__}"

        # Empty limits dict; limits are sent per-request.
        limits_dict: dict[str, Any] = {}

        self._proc = ctx.Process(
            target=_child_entry,
            args=(adapter_name, limits_dict, child_conn),
            daemon=True,
        )
        self._proc.start()
        child_conn.close()  # child owns its end; parent uses parent_conn

        # Wait for the child to be ready by sending a trivial probe.
        # If the child fails during import/setup, we detect it here.
        self._conn = parent_conn
        self._conn.send((b"__ping__",))

        try:
            kind, payload = self._conn.recv()
        except (EOFError, OSError):
            # Child died during startup.
            self._proc.join(timeout=5)
            exit_code = self._proc.exitcode
            raise DecoderTimeout(
                f"decoder child exited during startup (exit code {exit_code})"
            ) from None

        if kind == "error":
            exc = payload
            self._proc.join(timeout=5)
            if isinstance(exc, ImportError):
                raise DecoderTimeout(f"decoder child failed to import adapter: {exc}") from exc
            raise DecoderCrashed(
                f"decoder child failed during startup: {type(exc).__name__}: {exc}"
            ) from exc

    def decode(
        self,
        data: bytes,
        limits: DecodeLimits,
        timeout_seconds: float,
    ) -> DecodedImage:
        """Decode ``data`` in the child process with the given limits."""
        limits_dict = {
            "max_input_bytes": limits.max_input_bytes,
            "max_pixels": limits.max_pixels,
            "max_frames": limits.max_frames,
            "timeout_seconds": limits.timeout_seconds,
            "max_decoded_bytes": limits.max_decoded_bytes,
        }
        self._conn.send((data, limits_dict))

        result: list[Any] = []

        def _recv() -> None:
            try:
                result.append(self._conn.recv())
            except Exception as exc:  # noqa: BLE001 - capture any recv error
                result.append(exc)

        recv_thread = threading.Thread(target=_recv, daemon=True)
        recv_thread.start()
        recv_thread.join(timeout=timeout_seconds + 1)

        if recv_thread.is_alive():
            # Child hasn't responded within timeout - kill it.
            self._proc.kill()
            self._proc.join(timeout=5)
            raise DecoderTimeout(f"decoder exceeded {timeout_seconds}s wall-clock limit") from None

        result_item = result[0]
        if isinstance(result_item, Exception):
            raise result_item

        kind, payload = result_item

        if kind == "ok":
            return cast(DecodedImage, payload)

        # kind == "error"
        exc = payload
        if isinstance(exc, InputRejectedError):
            raise exc
        if isinstance(exc, DecoderError):
            raise exc
        if isinstance(exc, MemoryError):
            raise DecoderMemoryLimit("decoder exceeded memory ceiling") from exc
        if isinstance(exc, TimeoutError):
            raise DecoderTimeout("decoder exceeded time limit") from exc
        raise DecoderCrashed(f"decoder raised {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        """Shut down the child process cleanly."""
        if self._conn is not None:
            with contextlib.suppress(OSError, BrokenPipeError):
                self._conn.send((None,))
                self._conn.close()
        if self._proc is not None:
            self._proc.join(timeout=5)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(timeout=5)

    def __del__(self) -> None:
        """Ensure child process is cleaned up when the worker is garbage collected."""
        with contextlib.suppress(Exception):
            self.close()


class IsolatedDecoder:
    """Wrapper that runs an :class:`ImageDecoder` adapter in an isolated child process.

    When ``isolation`` is ``"process"`` (the default), decoding happens in a
    short-lived child with CPU, memory and wall-clock limits. A decoder crash, hang or
    memory blow-up affects only the worker; the parent reports a typed error
    (:exc:`DecoderCrashed`, :exc:`DecoderTimeout`, :exc:`DecoderMemoryLimit`) and
    continues.

    When ``isolation`` is ``"in_process"`` (development only), decoding runs
    directly in the calling process with no isolation. This mode must not be used
    in production.
    """

    def __init__(
        self,
        adapter: ImageDecoder,
        *,
        isolation: str = "process",
    ) -> None:
        self._adapter = adapter
        self._isolation = isolation
        self._formats = adapter.formats
        self._worker: _IsolatedWorker | None = None

    @property
    def formats(self) -> frozenset[str]:
        return self._formats

    def library_versions(self) -> dict[str, str]:
        return self._adapter.library_versions()

    def probe(self, data: bytes) -> str | None:
        return self._adapter.probe(data)

    def _get_worker(self) -> _IsolatedWorker:
        """Lazily create the isolated worker process."""
        if self._worker is None:
            self._worker = _IsolatedWorker(self._adapter, self._isolation)
        return self._worker

    def decode(
        self,
        data: bytes,
        *,
        source_sha256: str,
        limits: DecodeLimits,
    ) -> DecodedImage:
        """Decode ``data`` with limits enforced, either in-process or in a child."""
        if self._isolation == "in_process":
            return _decode_in_process(self._adapter, data, source_sha256, limits)
        worker = self._get_worker()
        try:
            return worker.decode(data, limits, limits.timeout_seconds)
        finally:
            worker.close()

    def close(self) -> None:
        """Shut down the isolated worker process if one was created."""
        if self._worker is not None:
            self._worker.close()
            self._worker = None

    def __del__(self) -> None:
        """Ensure child process is cleaned up when decoder is garbage collected."""
        with contextlib.suppress(Exception):
            self.close()
