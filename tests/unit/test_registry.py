from pathlib import Path

import pytest
from helpers import meta, registry_of

from photoproof.contracts.exceptions import PluginLoadError, RegistryError
from photoproof.core.pipeline.discovery import discover, implementation_digest
from photoproof.core.pipeline.registry import PluginRegistry

DEMO = ["demo.comparison", "demo.configurable", "demo.consumer", "demo.observer", "demo.source"]


def test_discovers_demo_plugins_deterministically() -> None:
    first = [d.package for d in discover("mockplugins.demo")]
    assert first == sorted(first)
    assert first == [d.package for d in discover("mockplugins.demo")]
    registry = PluginRegistry(discover("mockplugins.demo"), host_api="1.0.0")
    assert sorted(p.id for p in registry) == DEMO
    assert [p.discovery_index for p in registry] == sorted(p.discovery_index for p in registry)
    assert not registry.unavailable


def test_metadata_loading_is_lazy() -> None:
    import sys

    sys.modules.pop("mockplugins.demo.observer.processor", None)
    registry = PluginRegistry(discover("mockplugins.demo"), host_api="1.0.0")
    assert "mockplugins.demo.observer.processor" not in sys.modules
    registry.get("demo.observer").load_entry_point()
    assert "mockplugins.demo.observer.processor" in sys.modules


def test_broken_plugins_reported_unavailable() -> None:
    registry = PluginRegistry(discover("mockplugins.broken"), host_api="1.0.0")
    assert len(registry) == 0
    reasons = {u.plugin_id or u.package: u.reason for u in registry.unavailable}
    assert "version" in reasons["broken.bad_version"]
    assert "incompatible" in reasons["broken.bad_api"]
    assert "not found" in reasons["broken.no_processor"]
    assert "import failed" in reasons["mockplugins.broken.import_error"]


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(RegistryError, match=r"dup\.same"):
        PluginRegistry(discover("mockplugins.duplicate"), host_api="1.0.0")


def test_duplicate_ids_rejected_in_memory(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        registry_of(tmp_path, meta("a.one"), meta("a.one"))


@pytest.mark.parametrize(
    ("fields", "fragment"),
    [
        ({"version": "1.0"}, "version"),
        ({"priority": -1}, "priority"),
        ({"requires": ["a.self"]}, "itself"),
        ({"requires": ["b.x"], "after": ["b.x"]}, "both"),
        ({"outputs": []}, "outputs"),
        ({"outputs": ["input.target"]}, "reserved"),
        ({"entry_point": "../evil:run"}, "entry_point"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_metadata(tmp_path: Path, fields: dict[str, object], fragment: str) -> None:
    registry = registry_of(tmp_path, meta("a.self", **fields))
    assert len(registry) == 0
    assert fragment in registry.unavailable[0].reason


def test_missing_entry_point_attribute(tmp_path: Path) -> None:
    registry = PluginRegistry(discover("mockplugins.demo"), host_api="1.0.0")
    plugin = registry.get("demo.observer")
    broken = type(plugin)(
        plugin.metadata.model_copy(update={"entry_point": "processor:nope"}), plugin.package, 0, "x"
    )
    with pytest.raises(PluginLoadError, match="nope"):
        broken.load_entry_point()


def test_implementation_digest_changes_with_code(tmp_path: Path) -> None:
    (tmp_path / "processor.py").write_text("a = 1\n")
    before = implementation_digest(tmp_path)
    (tmp_path / "processor.py").write_text("a = 2\n")
    assert implementation_digest(tmp_path) != before
