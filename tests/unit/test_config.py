from pathlib import Path

import pytest
from helpers import REPO, update_settings, write_profile

from photoproof.contracts.exceptions import ConfigurationError, IdentityError
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.config.loader import load_identity, load_profiles, load_settings


def test_repository_configuration_is_valid() -> None:
    identity = load_identity(REPO)
    assert identity.application.version == "0.1.0"
    assert identity.application.name.lower() == identity.application.package
    load_settings(REPO, identity, environ={})
    assert {"standard", "comparison", "research"} <= set(load_profiles(REPO))


def test_package_name_mismatch(app_root: Path) -> None:
    path = app_root / "app.toml"
    path.write_text(path.read_text().replace('package = "photoproof"', 'package = "other"'))
    with pytest.raises(IdentityError, match=r"name.lower\(\)"):
        load_identity(app_root)


def test_missing_required_identity_field(app_root: Path) -> None:
    path = app_root / "app.toml"
    path.write_text(path.read_text().replace('release_channel = "development"', ""))
    with pytest.raises(IdentityError, match="release_channel"):
        load_identity(app_root)


def _settings(root: Path):  # type: ignore[no-untyped-def]
    return load_settings(root, load_identity(root), environ={})


def test_invalid_value_type(app_root: Path) -> None:
    update_settings(app_root, scheduling={"default_plugin_timeout_seconds": "soon"})
    with pytest.raises(ConfigurationError, match="default_plugin_timeout_seconds"):
        _settings(app_root)


def test_out_of_range_and_enum(app_root: Path) -> None:
    update_settings(app_root, logging={"level": "LOUD"}, resources={"max_input_bytes": 0})
    with pytest.raises(ConfigurationError) as error:
        _settings(app_root)
    assert "logging.level" in str(error.value) and "resources.max_input_bytes" in str(error.value)


def test_unknown_setting_rejected(app_root: Path) -> None:
    update_settings(app_root, cache={"enabeld": True})
    with pytest.raises(ConfigurationError, match="enabeld"):
        _settings(app_root)


def test_duplicate_yaml_key_rejected(app_root: Path) -> None:
    path = app_root / "settings.yaml"
    path.write_text(path.read_text() + "\ncache:\n  enabled: false\n")
    with pytest.raises(ConfigurationError, match="duplicate key"):
        _settings(app_root)


def test_duplicate_blacklist_entries(app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["a.b", "a.b"]})
    with pytest.raises(ConfigurationError, match="duplicate"):
        _settings(app_root)


def test_conflicting_policies_in_production(app_root: Path) -> None:
    update_settings(app_root, environment={"mode": "production"}, development={"debug": True})
    with pytest.raises(ConfigurationError) as error:
        _settings(app_root)
    assert "debug" in str(error.value) and "allow_blacklist_bypass" in str(error.value)


def test_blacklist_and_forbidden_overlap(app_root: Path) -> None:
    update_settings(
        app_root, plugins={"blacklist": ["a.b"]}, security={"forbidden_plugins": ["a.b"]}
    )
    with pytest.raises(ConfigurationError, match="both"):
        _settings(app_root)


def test_absolute_runtime_root_rejected(app_root: Path) -> None:
    update_settings(app_root, runtime={"root": "/etc"})
    with pytest.raises(ConfigurationError, match=r"runtime\.root"):
        _settings(app_root)


def test_schema_version_mismatch(app_root: Path) -> None:
    path = app_root / "settings.yaml"
    path.write_text(path.read_text().replace("schema_version: 1", "schema_version: 2"))
    with pytest.raises(ConfigurationError, match="schema_version 2 is not supported"):
        _settings(app_root)


def test_environment_overrides_are_allowlisted(app_root: Path) -> None:
    identity = load_identity(app_root)
    settings = load_settings(app_root, identity, environ={"PHOTOPROOF_LOG_LEVEL": "DEBUG"})
    assert settings.logging.level == "DEBUG"
    with pytest.raises(ConfigurationError, match="unsupported environment override"):
        load_settings(app_root, identity, environ={"PHOTOPROOF_CACHE": "off"})


def test_yaml_tags_are_not_executed(app_root: Path) -> None:
    path = app_root / "settings.yaml"
    path.write_text(
        path.read_text().replace("debug: false", "debug: !!python/object/apply:os.getcwd []")
    )
    with pytest.raises(ConfigurationError, match="malformed YAML"):
        _settings(app_root)


def test_profile_name_must_match_file(app_root: Path) -> None:
    write_profile(app_root, "custom")
    path = app_root / "configs" / "profiles" / "custom.yaml"
    path.write_text(path.read_text().replace("name: custom", "name: other"))
    with pytest.raises(ConfigurationError, match="must match the file name"):
        load_profiles(app_root)


def test_profile_cannot_enable_and_disable(app_root: Path) -> None:
    write_profile(app_root, "custom", plugins={"enable": ["a.b"], "disable": ["a.b"]})
    with pytest.raises(ConfigurationError, match="both enabled and disabled"):
        load_profiles(app_root)


def test_feature_flags() -> None:
    flags = FeatureFlags.build(
        {"cache.run_level": False, "plugin.demo.source": True},
        plugin_ids={"demo.source"},
        unknown="error",
    )
    assert flags.is_enabled("cache.run_level") is False
    assert flags.is_enabled("events.progress") is False  # default
    assert flags.plugin_override("demo.source") is True
    assert flags.plugin_override("demo.other") is None
    assert flags.snapshot()["cache.run_level"] is False
    with pytest.raises(KeyError):
        flags.is_enabled("plugin.demo.source")


def test_unknown_feature_flag() -> None:
    with pytest.raises(ConfigurationError, match="unknown feature flags: nope"):
        FeatureFlags.build({"nope": True}, plugin_ids=(), unknown="error")
    flags = FeatureFlags.build({"nope": True}, plugin_ids=(), unknown="warn")
    assert not flags.is_known("nope")
