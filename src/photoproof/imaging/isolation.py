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
import queue as queue_module
import signal
from multiprocessing.context import SpawnContext
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
    data: bytes,
    limits_dict: dict[str, Any],
    result_queue: mp.Queue[Any],
) -> None:
    """Run in the child process: set limits, decode, send result back."""
    try:
        _apply_limits(limits_dict)

        # Import the adapter lazily inside the child (spawn start method).
        module_name, class_name = adapter_name.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        adapter_cls = getattr(module, class_name)
        adapter = adapter_cls()

        limits = DecodeLimits(**limits_dict)
        result = adapter.decode(data, source_sha256="", limits=limits)
        result_queue.put(("ok", result))
    except Exception as exc:  # noqa: BLE001 - child must report any failure
        result_queue.put(("error", exc))


def _decode_in_process(
    adapter: ImageDecoder,
    data: bytes,
    source_sha256: str,
    limits: DecodeLimits,
) -> DecodedImage:
    """Decode in the current process (no isolation, development only)."""
    return adapter.decode(data, source_sha256=source_sha256, limits=limits)


def _decode_in_child(
    adapter: ImageDecoder,
    data: bytes,
    source_sha256: str,
    limits: DecodeLimits,
    timeout_seconds: float,
) -> DecodedImage:
    """Decode in a spawn-isolated child process with resource limits."""
    ctx: SpawnContext = mp.get_context("spawn")
    result_queue: mp.Queue[Any] = ctx.Queue()

    limits_dict = {
        "max_input_bytes": limits.max_input_bytes,
        "max_pixels": limits.max_pixels,
        "max_frames": limits.max_frames,
        "timeout_seconds": limits.timeout_seconds,
        "max_decoded_bytes": limits.max_decoded_bytes,
    }

    adapter_name = f"{adapter.__class__.__module__}.{adapter.__class__.__name__}"

    proc = ctx.Process(
        target=_child_entry,
        args=(adapter_name, data, limits_dict, result_queue),
        daemon=True,
    )
    proc.start()

    try:
        kind, payload = result_queue.get(timeout=timeout_seconds + 10)
    except queue_module.Empty:
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=5)
        raise DecoderTimeout(f"decoder exceeded {timeout_seconds}s wall-clock limit") from None

    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)

    exit_code = proc.exitcode
    if exit_code is not None and exit_code != 0:
        if exit_code < 0:
            signal_num = -exit_code
            try:
                signal_name = signal.Signals(signal_num).name
            except (ValueError, AttributeError):
                signal_name = str(signal_num)
            raise DecoderCrashed(f"decoder killed by signal {signal_num} ({signal_name})") from None
        raise DecoderCrashed(f"decoder exited with code {exit_code}") from None

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


class IsolatedDecoder:
    """Wrapper that runs an :class:`ImageDecoder` adapter in an isolated child process.

    When ``isolation`` is ``"process"`` (the default), decoding happens in a
    short-lived child with CPU, memory and wall-clock limits. A crash, hang or
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
        return _decode_in_child(self._adapter, data, source_sha256, limits, limits.timeout_seconds)
