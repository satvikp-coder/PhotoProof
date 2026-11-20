"""Runtime-root precedence and immutable packaged-default fallback without home writes."""

import shutil
from pathlib import Path

import pytest

from photoproof.contracts.exceptions import ConfigurationError, UnsafePathError
from photoproof.core.config import runtime
from photoproof.core.config.loader import (
    load_identity,
    load_plugin_config,
    load_profiles,
    load_settings,
)
from tests.helpers import REPO


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    defaults = tmp_path / "installed-defaults"
    defaults.mkdir()
    for name in ("app.toml", "settings.yaml"):
        shutil.copyfile(REPO / name, defaults / name)
    shutil.copytree(REPO / "configs", defaults / "configs")
    monkeypatch.setattr(runtime, "packaged_defaults", lambda: defaults)
    return defaults


def test_root_precedence_is_explicit_then_environment_then_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "packaged_defaults", lambda: None)
    explicit, home, checkout = (tmp_path / n for n in ("explicit", "home", "checkout"))
    assert (
        runtime.resolve_root(
            explicit, checkout_root=checkout, environ={"PHOTOPROOF_HOME": str(home)}
        )
        == explicit
    )
    assert (
        runtime.resolve_root(None, checkout_root=checkout, environ={"PHOTOPROOF_HOME": str(home)})
        == home
    )
    assert runtime.resolve_root(None, checkout_root=checkout, environ={}) == checkout
    assert not explicit.exists() and not home.exists()


@pytest.mark.parametrize("value", ["", "  ", "relative/path"])
def test_home_override_requires_an_absolute_path(tmp_path, value):
    with pytest.raises(ConfigurationError, match="absolute path"):
        runtime.resolve_root(None, checkout_root=tmp_path, environ={"PHOTOPROOF_HOME": value})


def test_packaged_defaults_are_read_without_materializing_or_mutating_home(tmp_path, packaged):
    home = tmp_path / "new-home"
    identity = load_identity(home)
    settings = load_settings(home, identity, environ={"PHOTOPROOF_HOME": str(home)})
    assert settings.profiles.default in load_profiles(home)
    assert load_plugin_config(home, "fusion.normalize") is not None
    assert not home.exists()
    # A malformed explicit file is not silently replaced with defaults.
    home.mkdir()
    (home / "settings.yaml").write_text("invalid: true", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(home, identity, environ={})


def test_init_is_repeatable_and_preserves_operator_configuration(tmp_path, packaged):
    home = tmp_path / "home"
    created = runtime.initialize_configuration(home, checkout_root=REPO)
    assert "settings.yaml" in created and "configs/profiles/standard.yaml" in created
    assert "app.toml" not in created
    custom = home / "settings.yaml"
    custom.write_text("operator config", encoding="utf-8")
    assert runtime.initialize_configuration(home, checkout_root=REPO) == ()
    assert custom.read_text(encoding="utf-8") == "operator config"
    assert (packaged / "settings.yaml").read_text(encoding="utf-8") != "operator config"


def test_configuration_rejects_runtime_path_traversal(tmp_path, packaged):
    with pytest.raises(UnsafePathError):
        runtime.configuration_path(tmp_path, "../outside.yaml")


def test_unknown_home_like_environment_override_is_still_rejected(tmp_path, packaged):
    with pytest.raises(ConfigurationError, match="unsupported environment override"):
        load_settings(tmp_path, load_identity(tmp_path), environ={"PHOTOPROOF_HOM": "oops"})


def test_installed_default_directory_is_not_a_temporary_directory(tmp_path, packaged, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user"))
    home = runtime.resolve_root(None, checkout_root=tmp_path / "site-packages", environ={})
    assert tmp_path / "user" in home.parents
    assert not home.exists()


def test_cli_root_and_init_preserve_existing_settings(tmp_path, app_root, capsys):
    from photoproof.cli import main

    selected = tmp_path / "selected"
    assert main(["--root", str(selected), "init"], root=app_root) == 0
    settings = selected / "settings.yaml"
    original = settings.read_bytes() + b"\n# operator note\n"
    settings.write_bytes(original)
    assert main(["--root", str(selected), "init"], root=app_root) == 0
    assert settings.read_bytes() == original
    assert main(["--root", str(selected), "status"], root=app_root) == 0
    assert (selected / "ARE" / "database").is_dir()
    assert not (app_root / "ARE").exists()


def test_installed_package_layout_requires_the_distribution_location(
    tmp_path, monkeypatch, app_root
):
    import importlib
    from types import SimpleNamespace

    from photoproof.contracts.exceptions import IdentityError

    module = importlib.import_module("photoproof.core.bootstrap")
    package = tmp_path / "site-packages" / "photoproof" / "__init__.py"
    monkeypatch.setattr(module.photoproof, "__file__", str(package))
    monkeypatch.setattr(
        module, "distribution", lambda name: SimpleNamespace(locate_file=lambda name: package)
    )
    module.validate_package_layout(load_identity(app_root))
    monkeypatch.setattr(
        module,
        "distribution",
        lambda name: SimpleNamespace(locate_file=lambda name: tmp_path / "other.py"),
    )
    with pytest.raises(IdentityError, match="differs from the installed"):
        module.validate_package_layout(load_identity(app_root))


def test_installed_identity_cannot_be_shadowed_by_old_runtime_copy(tmp_path, packaged):
    home = tmp_path / "home"
    home.mkdir()
    old = home / "app.toml"
    old.write_bytes(b"old operator identity is not a distribution identity")
    identity = load_identity(home)
    assert identity == load_identity(packaged)
    runtime.initialize_configuration(home, checkout_root=REPO)
    assert old.read_bytes() == b"old operator identity is not a distribution identity"
