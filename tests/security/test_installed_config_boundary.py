"""Configuration initialization preserves links and refuses paths outside its root."""

from pathlib import Path

import pytest

from photoproof.contracts.exceptions import UnsafePathError
from photoproof.core.config.runtime import initialize_configuration
from tests.helpers import REPO


def _link(source: Path, destination: Path) -> None:
    try:
        source.symlink_to(destination)
    except OSError as error:
        pytest.skip(f"cannot create a symbolic link on this host: {error}")


def test_init_preserves_an_existing_dangling_internal_link(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    destination = home / "operator-settings.yaml"
    link = home / "settings.yaml"
    _link(link, destination)
    created = initialize_configuration(home, checkout_root=REPO)
    assert "settings.yaml" not in created
    assert link.is_symlink() and not destination.exists()


def test_init_does_not_write_through_an_external_link(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(b"operator data")
    _link(home / "settings.yaml", outside)
    with pytest.raises(UnsafePathError):
        initialize_configuration(home, checkout_root=REPO)
    assert outside.read_bytes() == b"operator data"


@pytest.mark.parametrize("relative", ["../outside", "/outside", r"\outside"])
def test_runtime_root_cannot_escape_the_selected_home(app_root, relative):
    from photoproof.contracts.exceptions import ConfigurationError
    from photoproof.core.bootstrap import bootstrap

    settings = app_root / "settings.yaml"
    text = settings.read_text(encoding="utf-8").replace("root: ARE", f"root: {relative}", 1)
    settings.write_text(text, encoding="utf-8")
    with pytest.raises((UnsafePathError, ConfigurationError)):
        bootstrap(app_root, plugin_package="mockplugins.demo", environ={})
    assert not (app_root / "ARE").exists()
