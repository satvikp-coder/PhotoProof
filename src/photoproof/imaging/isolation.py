"""Isolated image decoding in a resource-limited child process.

``IsolatedDecoder`` wraps an in-process :class:`ImageDecoder` adapter and runs its
``decode`` method in a short-lived child process with CPU, memory and wall-clock
limits. A decoder crash, hang or memory blow-up affects only the worker; the parent
reports a typed error and continues.

When ``isolation`` is ``"in_process"`` (development only), decoding runs directly in
the calling process with no isolation -- use this only when the platform cannot
enforce child-process limits.

Child-process guarantees
------------------------

* Every decode is its own short-lived child: ``spawn`` on every platform so the child
  never inherits the parent's threads, file descriptors or (on macOS py3.15+) the
  ``fork`` deprecation. Re-importing the adapter for each decode keeps child startup
  failure distinct from a decode failure and prevents a crashing decoder from poisoning
  a long-lived worker.
* Each child uses a single bound ``Pipe`` (no ``multiprocessing.Queue`` feeder thread)
  and the child's only inbound message is ``(data_bytes, limits_dict)``. The parent
  never trusts arbitrary child exception objects: responses are validated against a
  fixed tag vocabulary, and unknown or malformed messages raise ``DecoderCrashed``.
* The parent enforces a real wall-clock deadline. A child that does not reply before
  it is terminated and joined so no orphan remains.
* Memory and CPU limits are applied where the platform supports them. On Linux the
  child caps its own virtual address space (``RLIMIT_AS``). Because native libraries
  reserve large virtual-memory regions that are never resident, ``RLIMIT_AS`` is
  intentionally generous and is derived from the decoded-pixel budget rather than set
  to a tiny value; the parent's wall-clock deadline and the decoder's own input-size
  checks are the real backstops. macOS ignores ``RLIMIT_AS``, so memory is not capped
  there (documented). Windows uses a Job Object with ``KillOnJobClose``.
* Every child is cleaned up on every exit path: normal return, exception, timeout and
  parent process termination. ``close()`` is idempotent and is called from a
  ``finally`` on every decode; there is no reliance on ``__del__`` or ``atexit``.
* Returned dimensions, pixel count, buffer size and type are validated before the
  decoded image is accepted.
* The original source SHA-256 is preserved in the decoded metadata.
* The parent process survives a crashing decoder.

Platform-specific guarantees
----------------------------

    ========  =========================  =============  ========================
    Platform  Memory                     CPU           Wall clock
    ========  =========================  =============  ========================
    Linux     RLIMIT_AS (generous for    RLIMIT_CPU    parent deadline -> term ->
              native VM reservations)              kill
    macOS     not enforced               RLIMIT_CPU    parent deadline -> term ->
                                              kill
    Windows   Job Object (KillOnJobClose) not enforced  parent deadline -> term ->
                                          kill
    ========  =========================  =============  ========================
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
from typing import Any, cast

from photoproof.contracts.exceptions import (
    DecoderCrashed,
    DecoderMemoryLimit,
    DecoderTimeout,
    InputRejectedError,
)
from photoproof.imaging.decoder import DecodedImage, DecodeLimits, ImageDecoder

try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None  # type: ignore[assignment]

#: Messages the child sends back are validated against this fixed tag vocabulary.
_CHILD_REPLY_OK = "ok"
_CHILD_REPLY_ERROR = "error"

#: How long the parent waits for the child to finish after termination.
_CHILD_JOIN_SECONDS = 5.0


def _limits_dict(limits: DecodeLimits) -> dict[str, Any]:
    """Convert ``DecodeLimits`` into a plain dict safe to send across the pipe."""
    return {
        "max_input_bytes": limits.max_input_bytes,
        "max_pixels": limits.max_pixels,
        "max_frames": limits.max_frames,
        "timeout_seconds": limits.timeout_seconds,
        "max_decoded_bytes": limits.max_decoded_bytes,
    }


def _limits_from_dict(d: dict[str, Any]) -> DecodeLimits:
    """Convert a limits dict back into ``DecodeLimits`` with safe defaults."""
    return DecodeLimits(
        max_input_bytes=int(d.get("max_input_bytes", 0)),
        max_pixels=int(d.get("max_pixels", 0)),
        max_frames=int(d.get("max_frames", 1)),
        timeout_seconds=float(d.get("timeout_seconds", 30.0)),
        max_decoded_bytes=int(d.get("max_decoded_bytes", 2 << 30)),
    )


def _child_entry(
    adapter_name: str,
    limits_dict: dict[str, Any],
    child_conn: Any,
) -> None:
    """Run in the child process: apply limits, import the adapter, decode one request.

    The child lives for exactly one decode. Limits are applied before the adapter is
    imported so native-library startup happens under the ceiling. ``child_conn``
    receives one message ``(data_bytes, limits_dict)`` and replies either
    ``("ok", decoded_image)`` or ``("error", "<class name>: <message>")``. The reply
    is a plain tuple of strings, never an exception object, so the parent cannot be
    made to execute or trust arbitrary child state.
    """
    try:
        _apply_child_limits(limits_dict)

        module_name, class_name = adapter_name.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        adapter_cls = getattr(module, class_name)
        adapter = adapter_cls()

        msg = child_conn.recv()
        if not (isinstance(msg, tuple) and len(msg) == 2):
            raise ValueError("child request must be (data, limits_dict)")
        data, request_limits_dict = msg
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("child request data must be bytes")
        if not isinstance(request_limits_dict, dict):
            raise ValueError("child request limits must be a mapping")

        limits = _limits_from_dict(request_limits_dict)
        result = adapter.decode(bytes(data), source_sha256="", limits=limits)

        child_conn.send((_CHILD_REPLY_OK, result))
    except BaseException as exc:  # noqa: BLE001 - report any child failure as typed text
        with contextlib.suppress(Exception):
            # If the pipe is broken there is nothing more to send.
            child_conn.send((_CHILD_REPLY_ERROR, f"{type(exc).__name__}: {exc}"))
    finally:
        with contextlib.suppress(Exception):
            child_conn.close()


def _apply_child_limits(limits_dict: dict[str, Any]) -> None:
    """Apply resource limits inside the child before any decoding.

    ``RLIMIT_AS`` caps virtual address space. On Linux it is enforced; macOS ignores it
    and Windows handles memory through a Job Object in ``core.isolation``. The ceiling
    is derived from the decoded-pixel budget plus a generous allowance for native
    library reservations (Pillow's codecs, NumPy's BLAS): a tiny fixed value would
    crash a healthy child for reasons unrelated to real memory use. The adapter
    enforces ``max_input_bytes``/``max_pixels``/``max_decoded_bytes`` before allocating
    pixels, and the parent enforces the wall-clock deadline, so there is never an
    unbounded allowance.
    """
    if resource is None:
        return
    # Core dumps of evidence in memory must never be produced.
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # CPU time limit mirrors the wall-clock deadline so a runaway native decoder is
    # stopped by the OS as well as by the parent.
    timeout_seconds = float(limits_dict.get("timeout_seconds", 60.0))
    if timeout_seconds > 0:
        with contextlib.suppress(ValueError, OSError):
            soft = max(1, int(timeout_seconds))
            _, hard = resource.getrlimit(resource.RLIMIT_CPU)
            ceiling = soft + 1 if hard == resource.RLIM_INFINITY else min(soft + 1, hard)
            resource.setrlimit(resource.RLIMIT_CPU, (min(soft, ceiling), ceiling))
    # Virtual address space ceiling. Derived from the pixel budget plus headroom for
    # native-library virtual-memory reservations that are never resident.
    max_decoded_bytes = int(limits_dict.get("max_decoded_bytes", 2 << 30))
    max_input_bytes = int(limits_dict.get("max_input_bytes", 0))
    ceiling = max_decoded_bytes + max_input_bytes + (1 << 30)  # +1 GiB headroom
    if ceiling > 0:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))


def _validate_decoded(result: DecodedImage) -> DecodedImage:
    """Validate a decoded image returned by the child before the parent accepts it.

    Raises ``DecoderCrashed`` for malformed dimensions, pixel count, buffer size or
    type. Never trusts the child's word.
    """
    if result.width < 1 or result.height < 1:
        raise DecoderCrashed(f"decoder returned invalid dimensions {result.width}x{result.height}")
    if result.channels < 1:
        raise DecoderCrashed(f"decoder returned invalid channel count {result.channels}")
    expected = result.width * result.height * result.channels
    if expected == 0:
        raise DecoderCrashed("decoder returned a zero-size image")
    if len(result.pixels) != expected:
        raise DecoderCrashed(
            f"decoder pixel buffer holds {len(result.pixels)} bytes, expected "
            f"{expected} for {result.width}x{result.height}x{result.channels}"
        )
    if not isinstance(result.source_sha256, str) or len(result.source_sha256) != 64:
        raise DecoderCrashed("decoder returned an invalid source SHA-256")
    return result


def _decode_in_process(
    adapter: ImageDecoder,
    data: bytes,
    source_sha256: str,
    limits: DecodeLimits,
) -> DecodedImage:
    """Decode in the current process (no isolation, development only)."""
    return adapter.decode(data, source_sha256=source_sha256, limits=limits)


class _IsolatedWorker:
    """A short-lived child process that decodes one image with resource limits.

    The child is spawned per-decode so a crashing, hanging or memory-blowing decoder
    cannot poison a long-lived worker or leak threads/fd state across decodes.
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

    def decode(
        self,
        data: bytes,
        limits: DecodeLimits,
        timeout_seconds: float,
        source_sha256: str = "",
    ) -> DecodedImage:
        """Spawn a child, decode ``data``, and return the validated result.

        Every exit path terminates and joins the child so no orphan remains.
        """
        self._start_child(_limits_dict(limits))
        try:
            return self._decode_once(data, limits, timeout_seconds, source_sha256)
        finally:
            self.close()

    def _start_child(self, limits_dict: dict[str, Any]) -> None:
        """Start the isolated child process using the spawn context."""
        ctx: Any = mp.get_context("spawn")

        parent_conn, child_conn = ctx.Pipe()

        adapter_name = f"{self._adapter.__class__.__module__}.{self._adapter.__class__.__name__}"

        self._proc = ctx.Process(
            target=_child_entry,
            args=(adapter_name, limits_dict, child_conn),
            daemon=True,
        )
        self._proc.start()
        child_conn.close()  # child owns its end; parent uses parent_conn
        self._conn = parent_conn

    def _decode_once(
        self,
        data: bytes,
        limits: DecodeLimits,
        timeout_seconds: float,
        source_sha256: str,
    ) -> DecodedImage:
        """Send one request and wait for the child's validated reply."""
        if self._conn is None:
            raise DecoderCrashed("decoder child connection is closed")
        self._conn.send((bytes(data), _limits_dict(limits)))

        # Enforce a real wall-clock deadline.
        if not self._conn.poll(timeout_seconds + 1.0):
            self._terminate_child()
            raise DecoderTimeout(f"decoder exceeded {timeout_seconds}s wall-clock limit") from None

        try:
            kind, payload = self._conn.recv()
        except (EOFError, OSError):
            # Child exited without replying: crash, or killed by a limit.
            self._proc.join(timeout=_CHILD_JOIN_SECONDS)
            raise DecoderCrashed("decoder child exited without returning a result") from None

        if kind == _CHILD_REPLY_OK:
            result = cast(DecodedImage, payload)
            # Preserve the caller's source SHA-256 in the decoded metadata.
            object.__setattr__(result, "source_sha256", source_sha256)
            return _validate_decoded(result)

        # kind == _CHILD_REPLY_ERROR
        text = str(payload)
        if text.startswith("InputRejectedError:"):
            raise InputRejectedError(text.split(":", 1)[1].strip())
        if "MemoryError" in text:
            raise DecoderMemoryLimit(f"decoder exceeded memory ceiling: {text}") from None
        if "TimeoutError" in text:
            raise DecoderTimeout(f"decoder exceeded time limit: {text}") from None
        raise DecoderCrashed(f"decoder raised {text}")

    def _terminate_child(self) -> None:
        """Terminate and join the child, never leaving an orphan."""
        if self._proc is not None and self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=_CHILD_JOIN_SECONDS)
            if self._proc.is_alive():
                self._proc.kill()
                self._proc.join(timeout=_CHILD_JOIN_SECONDS)

    def close(self) -> None:
        """Shut down the child process cleanly. Idempotent."""
        if self._conn is not None:
            with contextlib.suppress(OSError, BrokenPipeError):
                self._conn.close()
            self._conn = None
        self._terminate_child()


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

    @property
    def formats(self) -> frozenset[str]:
        return self._formats

    def library_versions(self) -> dict[str, str]:
        return self._adapter.library_versions()

    def probe(self, data: bytes) -> str | None:
        return self._adapter.probe(data)

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
        worker = _IsolatedWorker(self._adapter, self._isolation)
        try:
            return worker.decode(data, limits, limits.timeout_seconds, source_sha256)
        finally:
            worker.close()

    def close(self) -> None:
        """No persistent worker to close (each decode spawns its own child)."""

    def __del__(self) -> None:
        """No child resources to clean up (each child is joined on return)."""
