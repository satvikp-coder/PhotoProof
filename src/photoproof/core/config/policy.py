"""Plugin execution policy: decides whether each plugin is enabled, and why.

Precedence, highest first:

1. Non-bypassable security restrictions (``security.forbidden_plugins``)
2. Authorized ``--ignore-blacklist`` override (skips step 3 only)
3. Global plugin blacklist (``plugins.blacklist``) — overrides ``required=True``
4. Global feature flags (``plugin.<id>``)
5. Analysis-profile overrides, when ``plugins.allow_profile_overrides`` is true
   (``required_plugins`` count as ``enable``)
6. Plugin metadata default (``enabled_default``)
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from photoproof.contracts.exceptions import PolicyError
from photoproof.contracts.plugins import PluginMetadata
from photoproof.core.config.flags import FeatureFlags
from photoproof.core.config.validator import Profile, Settings


class DecisionSource(StrEnum):
    SECURITY = "security"
    BLACKLIST = "blacklist"
    BLACKLIST_BYPASSED = "blacklist_bypassed"
    FEATURE_FLAG = "feature_flag"
    PROFILE = "profile"
    DEFAULT = "default"


class OverrideOrigin(StrEnum):
    """Who asked for a blacklist bypass. Only the local CLI may."""

    LOCAL_CLI = "local_cli"
    WEB = "web"
    API = "api"


@dataclass(frozen=True)
class BlacklistOverride:
    origin: OverrideOrigin


@dataclass(frozen=True)
class PolicyDecision:
    plugin_id: str
    enabled: bool
    source: DecisionSource
    reason: str


def authorize_blacklist_override(
    settings: Settings, override: BlacklistOverride | None
) -> BlacklistOverride | None:
    """Validate a bypass request. Raises ``PolicyError`` rather than silently ignoring it."""
    if override is None:
        return None
    if override.origin is not OverrideOrigin.LOCAL_CLI:
        raise PolicyError(
            f"blacklist bypass is not permitted from origin {override.origin.value!r}"
        )
    if not settings.security.allow_blacklist_bypass:
        raise PolicyError("blacklist bypass is disabled by security.allow_blacklist_bypass")
    return override


def decide(
    meta: PluginMetadata,
    *,
    settings: Settings,
    flags: FeatureFlags,
    profile: Profile,
    override: BlacklistOverride | None,
) -> PolicyDecision:
    """Decide one plugin. ``override`` must come from ``authorize_blacklist_override``."""
    pid = meta.id
    if pid in settings.security.forbidden_plugins:
        return PolicyDecision(pid, False, DecisionSource.SECURITY, "forbidden by security policy")
    if pid in settings.plugins.blacklist:
        if override is None:
            return PolicyDecision(pid, False, DecisionSource.BLACKLIST, "blacklisted")
        bypassed = True
    else:
        bypassed = False

    flag = flags.plugin_override(pid)
    if flag is not None:
        decision = PolicyDecision(
            pid, flag, DecisionSource.FEATURE_FLAG, f"flag plugin.{pid}={flag}"
        )
    elif settings.plugins.allow_profile_overrides and (
        pid in profile.plugins.enable or pid in profile.required_plugins
    ):  # a profile that requires a plugin implicitly enables it
        decision = PolicyDecision(pid, True, DecisionSource.PROFILE, f"enabled by {profile.name}")
    elif settings.plugins.allow_profile_overrides and pid in profile.plugins.disable:
        decision = PolicyDecision(pid, False, DecisionSource.PROFILE, f"disabled by {profile.name}")
    else:
        decision = PolicyDecision(
            pid, meta.enabled_default, DecisionSource.DEFAULT, "metadata enabled_default"
        )
    if bypassed:
        return PolicyDecision(
            pid,
            decision.enabled,
            DecisionSource.BLACKLIST_BYPASSED,
            f"blacklist bypassed via --ignore-blacklist; then {decision.reason}",
        )
    return decision


def decide_all(
    plugins: Iterable[PluginMetadata],
    *,
    settings: Settings,
    flags: FeatureFlags,
    profile: Profile,
    override: BlacklistOverride | None,
    origin: OverrideOrigin = OverrideOrigin.LOCAL_CLI,
) -> dict[str, PolicyDecision]:
    if override is not None:
        # The caller's boundary wins even if a CLI-origin token is passed accidentally.
        if origin is not OverrideOrigin.LOCAL_CLI:
            authorize_blacklist_override(settings, BlacklistOverride(origin))
        authorize_blacklist_override(settings, override)
    return {
        meta.id: decide(meta, settings=settings, flags=flags, profile=profile, override=override)
        for meta in plugins
    }
