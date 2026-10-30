"""Test helpers (importable as ``helpers``; see pytest ``pythonpath``)."""

import io
import os
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from photoproof.core.pipeline.discovery import DiscoveredPlugin
from photoproof.core.pipeline.registry import PluginRegistry

REPO = Path(__file__).resolve().parents[1]
APP_VERSION: str = tomllib.loads((REPO / "app.toml").read_text())["application"]["version"]


def _load(text: str) -> Any:
    return YAML(typ="safe", pure=True).load(text)


def _dump(data: object) -> str:
    stream = io.StringIO()
    YAML(typ="safe", pure=True).dump(data, stream)
    return stream.getvalue()


def update_settings(root: Path, **sections: dict[str, Any]) -> None:
    path = root / "settings.yaml"
    data = _load(path.read_text())
    for section, values in sections.items():
        data[section].update(values)
    path.write_text(_dump(data))


def write_profile(root: Path, name: str, **fields: Any) -> None:
    profile = {"name": name, "description": "test profile", "mode": "single", **fields}
    (root / "configs" / "profiles" / f"{name}.yaml").write_text(_dump(profile))


def plugin_config(root: Path, plugin_id: str, **values: Any) -> None:
    (root / "configs" / "plugins" / f"{plugin_id}.yaml").write_text(_dump(values))


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


def make_blob_writable(path: Path) -> None:
    """Test-only owner tampering/cleanup: explicitly change the DACL before chmod.

    Windows read-only blob ACLs deliberately prohibit WRITE_ATTRIBUTES/WRITE_DATA.
    The owner can still change their DACL; this models that privileged action rather
    than weakening the actual corruption-detection assertions or production policy.
    Only pass disposable test artifacts, never original user evidence.
    """
    if os.name == "nt":
        from photoproof.core.storage.windows_acl import current_user_sid

        command = Path(os.environ["SYSTEMROOT"]) / "System32" / "icacls.exe"
        subprocess.run(
            [str(command), str(path), "/grant:r", f"*{current_user_sid()}:(F)"],
            shell=False,
            check=True,
            capture_output=True,
        )
    path.chmod(0o600)
