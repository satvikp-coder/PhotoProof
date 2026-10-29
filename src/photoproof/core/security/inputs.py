"""Input acceptance policy for files handed to PhotoProof."""

import hashlib
from pathlib import Path

from photoproof.contracts.exceptions import InputRejectedError

_CHUNK = 1 << 20


def hash_input_file(path: Path, *, max_bytes: int) -> tuple[str, int]:
    """Stream ``path`` and return ``(sha256, size)``.

    Only regular files are accepted, and reading stops as soon as ``max_bytes`` is
    exceeded, so oversized inputs are never fully read.
    """
    if not path.is_file():
        raise InputRejectedError(f"{path}: not a regular file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise InputRejectedError(
                    f"{path}: exceeds resources.max_input_bytes ({max_bytes} bytes)"
                )
            digest.update(chunk)
    return digest.hexdigest(), size
