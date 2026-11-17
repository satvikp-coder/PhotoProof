"""Plugin scaffold template generator.

Generates a consistent plugin skeleton from a family and name.
"""

from __future__ import annotations

import re
from pathlib import Path

from photoproof.core.security.paths import safe_join

#: Valid plugin id: ``<family>.<name>`` with lowercase, digits, dots, underscores and hyphens.
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*\.[a-z0-9][a-z0-9._-]*$")

#: Default priority by family.
_DEFAULT_PRIORITY = {
    "compression": 50,
    "copymove": 60,
    "metadata": 40,
    "noise": 70,
    "visual": 80,
}


class ScaffoldError(Exception):
    """Raised when the scaffold input is invalid or the target already exists."""


def validate_plugin_id(plugin_id: str) -> tuple[str, str]:
    """Validate and split a plugin id into ``(family, name)``."""
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise ScaffoldError(
            f"invalid plugin id '{plugin_id}': must be '<family>.<name>' with "
            "lowercase letters, digits, dots, underscores and hyphens"
        )
    parts = plugin_id.split(".", 1)
    return parts[0], parts[1]


def plugin_root(family: str, name: str, base: Path) -> Path:
    """The plugin package directory: ``src/photoproof/plugins/<family>/<name>/``."""
    return safe_join(base, "src", "photoproof", "plugins", family, name)


def _default_inputs(family: str) -> tuple[str, ...]:
    if family == "copymove":
        return ("copymove.matches", "copymove.keypoints", "image.decoded")
    return ("image.decoded",)


def _default_outputs(family: str) -> tuple[str, ...]:
    if family == "copymove":
        return (f"{family}.result", f"{family}.result_stats")
    return (f"{family}.result",)


def _default_requires(family: str) -> tuple[str, ...]:
    if family == "copymove":
        return ("copymove.matching", "copymove.sift_keypoints")
    return ()


from .generator import generate as generate  # noqa: E402
