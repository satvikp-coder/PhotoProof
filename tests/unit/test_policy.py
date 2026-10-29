from pathlib import Path

import pytest
from helpers import update_settings

from photoproof.contracts.exceptions import PolicyError
from photoproof.contracts.plugins import PluginMetadata
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.config.loader import load_identity, load_profiles, load_settings
from photoproof.core.config.policy import (
    BlacklistOverride,
    DecisionSource,
    OverrideOrigin,
    authorize_blacklist_override,
    decide,
)
from photoproof.core.config.validator import Profile

CLI = BlacklistOverride(OverrideOrigin.LOCAL_CLI)


def _plugin(**fields: object) -> PluginMetadata:
    base = {
        "id": "x.plugin",
        "name": "x",
        "description": "x",
        "version": "1.0.0",
        "api_version": "1.0.0",
        "priority": 0,
        "outputs": ["x.out"],
    }
    return PluginMetadata.model_validate({**base, **fields})


def _ctx(root: Path, *, flags: dict[str, bool] | None = None, profile: dict | None = None):  # type: ignore[no-untyped-def,type-arg]
    settings = load_settings(root, load_identity(root), environ={})
    profile_model = Profile.model_validate(
        {"name": "t", "description": "t", "mode": "single", **(profile or {})}
    )
    ff = FeatureFlags.build(flags or {}, plugin_ids={"x.plugin"}, unknown="error")
    return {"settings": settings, "flags": ff, "profile": profile_model}


def test_metadata_default(app_root: Path) -> None:
    decision = decide(_plugin(enabled_default=False), override=None, **_ctx(app_root))
    assert (decision.enabled, decision.source) == (False, DecisionSource.DEFAULT)


def test_profile_overrides_default(app_root: Path) -> None:
    ctx = _ctx(app_root, profile={"plugins": {"enable": ["x.plugin"]}})
    decision = decide(_plugin(enabled_default=False), override=None, **ctx)
    assert (decision.enabled, decision.source) == (True, DecisionSource.PROFILE)


def test_profile_overrides_can_be_disallowed(app_root: Path) -> None:
    update_settings(app_root, plugins={"allow_profile_overrides": False})
    ctx = _ctx(app_root, profile={"plugins": {"disable": ["x.plugin"]}})
    assert decide(_plugin(), override=None, **ctx).enabled is True


def test_flag_overrides_profile(app_root: Path) -> None:
    ctx = _ctx(
        app_root, flags={"plugin.x.plugin": False}, profile={"plugins": {"enable": ["x.plugin"]}}
    )
    decision = decide(_plugin(), override=None, **ctx)
    assert (decision.enabled, decision.source) == (False, DecisionSource.FEATURE_FLAG)


def test_blacklist_overrides_flags_and_required(app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["x.plugin"]})
    ctx = _ctx(app_root, flags={"plugin.x.plugin": True})
    decision = decide(_plugin(required=True), override=None, **ctx)
    assert (decision.enabled, decision.source) == (False, DecisionSource.BLACKLIST)


def test_authorized_blacklist_bypass(app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["x.plugin"]})
    ctx = _ctx(app_root)
    override = authorize_blacklist_override(ctx["settings"], CLI)
    decision = decide(_plugin(), override=override, **ctx)
    assert (decision.enabled, decision.source) == (True, DecisionSource.BLACKLIST_BYPASSED)


def test_bypass_does_not_force_enable(app_root: Path) -> None:
    update_settings(app_root, plugins={"blacklist": ["x.plugin"]})
    ctx = _ctx(app_root, flags={"plugin.x.plugin": False})
    decision = decide(_plugin(), override=authorize_blacklist_override(ctx["settings"], CLI), **ctx)
    assert decision.enabled is False


@pytest.mark.parametrize("origin", [OverrideOrigin.WEB, OverrideOrigin.API])
def test_unauthorized_bypass_origin(app_root: Path, origin: OverrideOrigin) -> None:
    with pytest.raises(PolicyError, match="not permitted"):
        authorize_blacklist_override(_ctx(app_root)["settings"], BlacklistOverride(origin))


def test_bypass_disabled_by_settings(app_root: Path) -> None:
    update_settings(app_root, security={"allow_blacklist_bypass": False})
    with pytest.raises(PolicyError, match="disabled"):
        authorize_blacklist_override(_ctx(app_root)["settings"], CLI)


def test_security_restriction_is_not_bypassable(app_root: Path) -> None:
    update_settings(app_root, security={"forbidden_plugins": ["x.plugin"]})
    ctx = _ctx(app_root, flags={"plugin.x.plugin": True})
    decision = decide(_plugin(), override=authorize_blacklist_override(ctx["settings"], CLI), **ctx)
    assert (decision.enabled, decision.source) == (False, DecisionSource.SECURITY)


def test_shipped_profiles_load(app_root: Path) -> None:
    assert load_profiles(app_root)["comparison"].mode.value == "comparison"
