"""Compatibility rules between the host application and plugins/configuration."""

from photoproof.versioning.version import SemVer


def plugin_api_compatible(plugin_api: str, host_api: str) -> bool:
    """A plugin built for API ``X.Y.*`` runs on a host with API ``X.Z.*`` where ``Z >= Y``.

    During ``0.x`` the minor version is treated as breaking, mirroring the strict
    versioning policy.
    """
    plugin, host = SemVer.parse(plugin_api), SemVer.parse(host_api)
    if plugin.major != host.major:
        return False
    if host.major == 0:
        return plugin.minor == host.minor
    return plugin.minor <= host.minor


def config_schema_compatible(settings_schema: int, app_schema: int) -> bool:
    """Settings must declare exactly the schema version this release understands."""
    return settings_schema == app_schema
