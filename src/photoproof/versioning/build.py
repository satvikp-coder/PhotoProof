"""Runtime build information recorded in manifests and diagnostics."""

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def distribution_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def git_commit(root: Path) -> str | None:
    """Read the checked-out commit without spawning git. ``None`` outside a checkout."""
    head = root / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
        if not ref.startswith("ref: "):
            return ref
        ref_path = root / ".git" / ref[5:]
        if ref_path.is_file():
            return ref_path.read_text(encoding="utf-8").strip()
        packed = root / ".git" / "packed-refs"
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref[5:]):
                return line.split(" ", 1)[0]
    except OSError:
        return None
    return None


def build_info(
    root: Path, distributions: tuple[str, ...] = ("pydantic", "PyYAML")
) -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "platform": platform.system(),
        "machine": platform.machine(),
        "photoproof": distribution_version("photoproof"),
        "git_commit": git_commit(root),
        "distributions": {name: distribution_version(name) for name in distributions},
    }
