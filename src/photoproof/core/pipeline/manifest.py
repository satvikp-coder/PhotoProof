"""Execution manifests: a deterministic record of what a run computed and how.

``computational_fingerprint`` hashes only reproducible state; run IDs, timestamps,
messages and tracebacks are excluded so identical computations fingerprint equally.
"""

from collections.abc import Mapping, Sequence

from photoproof.core.storage.store import canonical_json, sha256_bytes

MANIFEST_VERSION = 1

_VOLATILE = {"run_id", "created_at", "finished_at", "cache", "computational_fingerprint"}


def computational_fingerprint(manifest: Mapping[str, object]) -> str:
    stable = {k: v for k, v in manifest.items() if k not in _VOLATILE}
    executions = stable.get("executions")
    if isinstance(executions, Mapping):
        stable["executions"] = {
            pid: {"state": ex["state"], "failure_category": ex["failure_category"]}
            for pid, ex in executions.items()
        }
    stable.pop("software", None)  # git commit / interpreter patch level are recorded, not hashed
    return sha256_bytes(canonical_json(stable))


def build_manifest(
    *,
    run_id: str,
    created_at: str,
    finished_at: str,
    application: Mapping[str, object],
    status: str,
    mode: str,
    profile: str,
    inputs: Sequence[Mapping[str, object]],
    pipeline_fingerprint: str | None,
    cache: Mapping[str, object],
    effective_settings: Mapping[str, object] | None,
    feature_flags: Mapping[str, bool],
    policy: Mapping[str, Mapping[str, object]],
    blacklist_override: bool,
    plugins: Mapping[str, Mapping[str, object]],
    dependency_graph: Mapping[str, object],
    execution_order: Sequence[str],
    executions: Mapping[str, Mapping[str, object]],
    blocked_reasons: Sequence[str],
    random_seed: int,
    artifacts: Sequence[Mapping[str, object]],
    outputs: Mapping[str, object],
    software: Mapping[str, object],
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "finished_at": finished_at,
        "application": dict(application),
        "status": status,
        "mode": mode,
        "profile": profile,
        "inputs": [dict(i) for i in inputs],
        "pipeline_fingerprint": pipeline_fingerprint,
        "cache": dict(cache),
        "effective_settings": dict(effective_settings) if effective_settings is not None else None,
        "feature_flags": dict(feature_flags),
        "policy": {k: dict(v) for k, v in sorted(policy.items())},
        "blacklist_override": blacklist_override,
        "plugins": {k: dict(v) for k, v in sorted(plugins.items())},
        "dependency_graph": dict(dependency_graph),
        "execution_order": list(execution_order),
        "executions": {k: dict(v) for k, v in executions.items()},
        "blocked_reasons": list(blocked_reasons),
        "random_seed": random_seed,
        "artifacts": sorted((dict(a) for a in artifacts), key=lambda a: str(a["sha256"])),
        "outputs": dict(sorted(outputs.items())),
        "software": dict(software),
    }
    manifest["computational_fingerprint"] = computational_fingerprint(manifest)
    return manifest
