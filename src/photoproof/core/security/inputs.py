"""Input acceptance policy for files handed to PhotoProof."""

import os
import stat
from pathlib import Path
from typing import BinaryIO

from photoproof.contracts.exceptions import InputRejectedError

# O_NONBLOCK: opening a FIFO must not hang; it is rejected by the fstat check below.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)


def open_regular_file(path: Path) -> BinaryIO:
    """Open ``path`` for reading, accepting only an existing regular file.

    Symlinks, directories, FIFOs and devices raise ``InputRejectedError``. The type is
    checked on the opened file, so swapping the path afterwards cannot change the answer.
    """
    rejected = f"{path}: not a regular file; pass a readable file, not a symlink or directory"
    try:
        if path.is_symlink():  # O_NOFOLLOW does not exist on Windows
            raise InputRejectedError(rejected)
        descriptor = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError) as error:
        raise InputRejectedError(f"{rejected} ({error})") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise InputRejectedError(rejected)
    return os.fdopen(descriptor, "rb")
