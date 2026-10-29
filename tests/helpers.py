"""Test helpers (importable as ``helpers``; see pytest ``pythonpath``)."""

import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

from photoproof.core.pipeline.discovery import DiscoveredPlugin
from photoproof.core.pipeline.registry import PluginRegistry

REPO = Path(__file__).resolve().parents[1]


def update_settings(root: Path, **sections: dict[str, Any]) -> None:
    path = root / "settings.yaml"
    data = yaml.safe_load(path.read_text())
    for section, values in sections.items():
        data[section].update(values)
    path.write_text(yaml.safe_dump(data))


def write_profile(root: Path, name: str, **fields: Any) -> None:
    profile = {"name": name, "description": "test profile", "mode": "single", **fields}
    (root / "configs" / "profiles" / f"{name}.yaml").write_text(yaml.safe_dump(profile))


def plugin_config(root: Path, plugin_id: str, **values: Any) -> None:
    (root / "configs" / "plugins" / f"{plugin_id}.yaml").write_text(yaml.safe_dump(values))


def meta(pid: str, **fields: Any) -> dict[str, Any]:
    base = {
        "id": pid,
        "name": pid,
        "description": "test plugin",
        "version": "1.0.0",
        "api_version": "1.0.0",
        "priority": 10,
        "outputs": [pid + ".out"],
    }
    base.update(fields)
    return base


def registry_of(tmp_path: Path, *metadata: dict[str, Any]) -> PluginRegistry:
    """Registry over throwaway plugin packages written to ``tmp_path``.

    Each plugin's ``run`` returns ``RESULTS[plugin_id]`` (or ``{}``), so executor
    tests can control outputs without fixture packages.
    """
    root_name = f"vplugins_{uuid.uuid4().hex[:8]}"
    base = tmp_path / "plugin_roots" / root_name
    base.mkdir(parents=True)
    (base / "__init__.py").write_text("")
    entries = []
    for index, raw in enumerate(metadata):
        directory = base / f"p{index}"
        directory.mkdir()
        (directory / "__init__.py").write_text("")
        (directory / "processor.py").write_text(
            "from helpers import RESULTS\n\n"
            "def run(ctx):\n"
            "    behaviour = RESULTS.get(ctx.plugin_id, {})\n"
            "    return behaviour(ctx) if callable(behaviour) else behaviour\n"
        )
        entries.append(DiscoveredPlugin(f"{root_name}.p{index}", index, raw, directory))
    if str(base.parent) not in sys.path:
        sys.path.insert(0, str(base.parent))
    return PluginRegistry(entries, host_api="1.0.0")


RESULTS: dict[str, Any] = {}
