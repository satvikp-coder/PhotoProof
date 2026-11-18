"""Bounded reads authorized by declared current-run producer artifacts."""

import hashlib
import os
from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.exceptions import (
    ConfigurationError,
    InputRejectedError,
    PolicyError,
    StorageError,
)
from photoproof.contracts.plugins import (
    ArtifactReader,
    ArtifactSelector,
    validate_artifact_selector,
)
from photoproof.core.security.inputs import open_regular_file
from photoproof.core.storage.store import RuntimeStorage

_CHUNK = 1 << 20


def _reference(value: object) -> ArtifactRef | None:
    for _ in range(2):
        if isinstance(value, Mapping) and set(value) == {"artifact"}:
            value = value["artifact"]
        else:
            break
    if isinstance(value, ArtifactRef):
        return value
    if not isinstance(value, Mapping):
        return None
    if not set(ArtifactRef.model_fields).issubset(value):
        return None
    try:
        return ArtifactRef.model_validate(value, strict=True)
    except ValidationError:
        return None


def make_artifact_reader(
    *,
    inputs: Mapping[str, object],
    producers: Mapping[str, str],
    staged: Mapping[str, Sequence[ArtifactRef]],
    storage: RuntimeStorage,
    max_bytes: int,
) -> ArtifactReader:
    """Bind immutable authorization records before handing a capability to a plugin.

    The controller supplies only resolved outputs of successful producers. Exact
    staged writer records prevent invented or transitive cross-producer references.
    This is an API boundary for trusted plugins, not an in-process Python sandbox.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ConfigurationError("artifact read ceiling must be a positive integer")
    allowed: dict[tuple[str, ArtifactSelector], ArtifactRef] = {}

    def authorize(name: str, value: object, selector: ArtifactSelector) -> None:
        ref = _reference(value)
        if ref is not None:
            producer = producers.get(name)
            if producer == ref.producer and ref in staged.get(producer, ()):
                allowed[name, selector] = ref
            return
        if len(selector) >= 8:
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(key, str):
                    authorize(name, child, (*selector, key))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                authorize(name, child, (*selector, index))

    for name, value in inputs.items():
        authorize(name, value, ())

    def read(name: str, requested: int, selector: ArtifactSelector = ()) -> bytes:
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
            raise PolicyError("artifact max_bytes must be a positive integer")
        key = name, validate_artifact_selector(selector)
        if key not in allowed:
            raise PolicyError(f"input {name!r} is not an authorized current-run artifact")
        ref = allowed[key]
        limit = min(max_bytes, requested)
        if ref.size > limit:
            raise PolicyError(f"artifact input {name!r} exceeds the {limit}-byte read ceiling")
        path = storage.blob_path("artifacts", ref.sha256)
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        try:
            with open_regular_file(path) as stream:
                if os.fstat(stream.fileno()).st_size != ref.size:
                    raise StorageError(
                        f"artifact input {name!r} has changed size; preserve and inspect it"
                    )
                while True:
                    chunk = stream.read(min(_CHUNK, limit - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise StorageError(f"artifact input {name!r} grew beyond its read ceiling")
                    digest.update(chunk)
                    chunks.append(chunk)
        except (OSError, InputRejectedError) as error:
            raise StorageError(
                f"artifact input {name!r} cannot be read; preserve and inspect it"
            ) from error
        if total != ref.size or digest.hexdigest() != ref.sha256:
            raise StorageError(
                f"artifact input {name!r} failed size/hash verification; preserve it"
            )
        return b"".join(chunks)

    return read
