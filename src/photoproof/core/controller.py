"""Analysis controller: turns an ``AnalysisRequest`` into an ``AnalysisResult``.

Flow: resolve profile → authorize overrides → decide policy → plan → (cache lookup)
→ execute → (cache store) → write manifest → persist. The controller is generic: it
never knows what any individual forensic plugin computes.
"""

import copy
import logging
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from photoproof.contracts.analysis import AnalysisRequest, AnalysisResult, PluginSummary
from photoproof.contracts.artifacts import ArtifactRef
from photoproof.contracts.enums import AnalysisMode, ExecutionState, RunStatus
from photoproof.contracts.events import EventType
from photoproof.contracts.exceptions import ConfigurationError, InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.core.bootstrap import Application
from photoproof.core.cache.run_cache import RunCache, cache_key
from photoproof.core.config.loader import PLUGIN_CONFIG_DIR
from photoproof.core.config.policy import (
    BlacklistOverride,
    PolicyDecision,
    authorize_blacklist_override,
    decide_all,
)
from photoproof.core.config.validator import Profile
from photoproof.core.pipeline.executor import ExecutionReport, SequentialExecutor, event_for
from photoproof.core.pipeline.manifest import build_manifest
from photoproof.core.pipeline.planner import ExecutionPlan, plan
from photoproof.core.pipeline.registry import RegisteredPlugin
from photoproof.core.storage.store import sha256_bytes, sha256_file
from photoproof.versioning.build import build_info, distribution_version


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AnalysisController:
    def __init__(self, app: Application) -> None:
        if not app.started:
            raise RuntimeError("Application.start() must run (after preflight) before analysis")
        self.app = app
        self.cache = RunCache(app.storage, app.database())

    # ------------------------------------------------------------------ public

    def resolve_profile(self, name: str | None, *, has_reference: bool) -> tuple[str, Profile]:
        """Return the profile to use, or raise if the request cannot run under it."""
        app = self.app
        profile_name = name or app.settings.profiles.default
        if profile_name not in app.profiles:
            raise ConfigurationError(
                f"unknown profile {profile_name!r}; known: {sorted(app.profiles)}"
            )
        profile = app.profiles[profile_name]
        if profile.mode is AnalysisMode.COMPARISON and not has_reference:
            raise InputRejectedError(f"profile {profile.name!r} requires a reference image")
        if profile.mode is AnalysisMode.SINGLE and has_reference:
            raise InputRejectedError(f"profile {profile.name!r} does not accept a reference image")
        return profile_name, profile

    def analyze(
        self, request: AnalysisRequest, *, override: BlacklistOverride | None = None
    ) -> AnalysisResult:
        app = self.app
        profile_name, profile = self.resolve_profile(
            request.profile, has_reference=request.reference is not None
        )
        override = authorize_blacklist_override(app.settings, override)

        run_id = uuid.uuid4().hex
        created_at = _now()
        decisions = decide_all(
            (p.metadata for p in app.registry),
            settings=app.settings,
            flags=app.flags,
            profile=profile,
            override=override,
        )
        if override is not None:
            bypassed = sorted(
                p for p, d in decisions.items() if d.source.value == "blacklist_bypassed"
            )
            app.bus.emit(
                EventType.POLICY_OVERRIDE,
                run_id=run_id,
                data={
                    "override": "ignore_blacklist",
                    "origin": override.origin.value,
                    "bypassed_plugins": bypassed,
                },
            )
        execution_plan = plan(app.registry, decisions, profile=profile, settings=app.settings)

        run_inputs: dict[str, object] = {"input.target": request.target.model_dump(mode="json")}
        if request.reference is not None:
            run_inputs["input.reference"] = request.reference.model_dump(mode="json")

        use_cache = bool(
            app.settings.cache.enabled
            and app.flags.is_enabled("cache.run_level")
            and request.use_cache
            and not execution_plan.blocked
            and execution_plan.order
        )
        key = (
            self._cache_key(request, profile_name, execution_plan, decisions) if use_cache else None
        )
        app.bus.emit(
            EventType.RUN_CREATED,
            run_id=run_id,
            data={"mode": profile.mode.value, "profile": profile_name, "cache_key": key},
        )
        try:
            return self._run(
                request,
                run_id,
                created_at,
                profile_name,
                execution_plan,
                decisions,
                override,
                run_inputs,
                key,
            )
        except BaseException as error:
            self._finalize_unfinished(run_id, error)
            raise

    def _run(
        self,
        request: AnalysisRequest,
        run_id: str,
        created_at: str,
        profile_name: str,
        execution_plan: ExecutionPlan,
        decisions: Mapping[str, PolicyDecision],
        override: BlacklistOverride | None,
        run_inputs: Mapping[str, object],
        key: str | None,
    ) -> AnalysisResult:
        app = self.app
        profile = app.profiles[profile_name]
        artifacts: list[ArtifactRef] = []
        executions: dict[str, dict[str, Any]] = {}
        outputs: dict[str, Any] = {}
        cache_hit = False
        if execution_plan.blocked:
            status = RunStatus.BLOCKED
        elif not execution_plan.order:
            status = RunStatus.NO_DETECTORS
        else:
            cached = self.cache.lookup(key) if key else None
            if key:
                app.bus.emit(
                    EventType.CACHE_HIT if cached else EventType.CACHE_MISS,
                    run_id=run_id,
                    data={"cache_key": key},
                )
            if cached:
                cache_hit = True
                status = RunStatus.COMPLETED
                executions, outputs = cached.executions, cached.outputs
                artifacts = list(cached.artifacts)
                self._record_cached(run_id, executions, artifacts)
            else:
                app.bus.emit(
                    EventType.RUN_STARTED, run_id=run_id, data={"order": list(execution_plan.order)}
                )
                report = self._execute(run_id, execution_plan, run_inputs, artifacts)
                status = report.status(execution_plan.required)
                executions = {pid: ex.to_dict() for pid, ex in report.executions.items()}
                outputs = report.outputs
                if key and status is RunStatus.COMPLETED:
                    self.cache.store(
                        key,
                        run_id=run_id,
                        executions=executions,
                        outputs=outputs,
                        artifacts=artifacts,
                    )

        manifest = build_manifest(
            run_id=run_id,
            created_at=created_at,
            finished_at=_now(),
            application={
                "name": app.identity.application.name,
                "version": app.identity.application.version,
                "plugin_api_version": app.identity.application.plugin_api_version,
            },
            status=status.value,
            mode=profile.mode.value,
            profile=profile_name,
            inputs=[d.model_dump(mode="json") for d in (request.target, request.reference) if d],
            pipeline_fingerprint=execution_plan.fingerprint(),
            cache={"key": key, "hit": cache_hit},
            effective_settings=(
                app.settings.model_dump(mode="json")
                if app.settings.reporting.include_effective_settings
                else None
            ),
            feature_flags=app.flags.snapshot(),
            policy={
                pid: {"enabled": d.enabled, "source": d.source.value, "reason": d.reason}
                for pid, d in decisions.items()
            },
            blacklist_override=override is not None,
            plugins=self._plugin_identities(execution_plan),
            dependency_graph=execution_plan.graph(),
            execution_order=execution_plan.order,
            executions=executions,
            blocked_reasons=execution_plan.blocked_reasons,
            random_seed=app.settings.scheduling.random_seed,
            artifacts=[a.model_dump(mode="json") for a in artifacts],
            outputs=outputs,
            software={
                **build_info(app.root),
                "image_decoders": app.decoders.library_versions(),
            },
        )
        manifest_path = app.storage.write_json(manifest, "manifests", f"{run_id}.json")
        manifest_sha = sha256_bytes(manifest_path.read_bytes())
        app.bus.emit(
            EventType.RUN_ABORTED if status is RunStatus.ABORTED else EventType.RUN_COMPLETED,
            run_id=run_id,
            data={"status": status.value, "cache_hit": cache_hit, "manifest_sha256": manifest_sha},
        )
        return AnalysisResult(
            run_id=run_id,
            status=status,
            mode=profile.mode,
            profile=profile_name,
            cache_hit=cache_hit,
            plugins=tuple(
                PluginSummary(
                    plugin_id=pid,
                    state=str(ex["state"]),
                    failure_category=ex.get("failure_category"),
                    message=ex.get("message"),
                )
                for pid, ex in executions.items()
            ),
            outputs=outputs,
            blocked_reasons=execution_plan.blocked_reasons,
            manifest_path=str(manifest_path),
        )

    # ----------------------------------------------------------------- helpers

    def _finalize_unfinished(self, run_id: str, error: BaseException) -> None:
        """Best effort: mark a run ABORTED when it dies before finishing normally, so it
        is never left in a non-terminal state. Secondary failures are logged; the
        original exception is re-raised by the caller."""
        try:
            self.app.bus.emit(
                EventType.RUN_ABORTED,
                run_id=run_id,
                data={
                    "status": RunStatus.ABORTED.value,
                    "cache_hit": False,
                    "manifest_sha256": None,
                    "error": repr(error),
                },
            )
        except Exception:
            logging.getLogger(__name__).exception("could not record run %s as aborted", run_id)

    def _record_cached(
        self,
        run_id: str,
        executions: Mapping[str, Mapping[str, object]],
        artifacts: list[ArtifactRef],
    ) -> None:
        """Persist plugin states and artifact references reused from the cache."""
        app = self.app
        for pid, ex in executions.items():
            state = ExecutionState(str(ex["state"]))
            app.bus.emit(
                event_for(state, started=ex.get("started_at") is not None),
                run_id=run_id,
                plugin_id=pid,
                data={**ex, "cached": True},
            )
        for ref in artifacts:
            app.bus.emit(
                EventType.ARTIFACT_CREATED,
                run_id=run_id,
                plugin_id=ref.producer,
                data={**ref.model_dump(mode="json"), "cached": True},
            )

    def _execute(
        self,
        run_id: str,
        execution_plan: ExecutionPlan,
        run_inputs: Mapping[str, object],
        artifacts: list[ArtifactRef],
    ) -> ExecutionReport:
        app = self.app
        executor = SequentialExecutor(
            bus=app.bus,
            on_required_failure=app.settings.failure_policy.on_required_failure,
            default_timeout_seconds=app.settings.scheduling.default_plugin_timeout_seconds,
        )

        # Artifacts are staged per plugin and recorded only after the plugin ends in
        # SUCCESS, on this (main) thread. A timed-out plugin that keeps writing in the
        # background therefore never adds artifacts to the run.
        staged: dict[str, list[ArtifactRef]] = {}

        def invoke(plugin: RegisteredPlugin, inputs: Mapping[str, object]) -> Mapping[str, object]:
            entry = plugin.load_entry_point()
            config = plugin.load_config(app.root)

            def write_artifact(name: str, data: bytes, media_type: str) -> ArtifactRef:
                if any(ref.name == name for ref in staged.get(plugin.id, [])):
                    raise ValueError(f"{plugin.id}: artifact name {name!r} already written")
                sha = app.storage.put_blob("artifacts", data)
                ref = ArtifactRef(
                    sha256=sha, name=name, media_type=media_type, size=len(data), producer=plugin.id
                )
                staged.setdefault(plugin.id, []).append(ref)
                return ref

            ctx = PluginContext(
                run_id=run_id,
                plugin_id=plugin.id,
                # Deep copy: a plugin must not be able to mutate another plugin's outputs.
                inputs=copy.deepcopy(dict(inputs)),
                config=config,
                seed=app.settings.scheduling.random_seed,
                write_artifact=write_artifact,
                logger=logging.getLogger(f"photoproof.plugins.{plugin.id}"),
            )
            return entry(ctx)

        report = executor.execute(
            execution_plan, run_id=run_id, run_inputs=run_inputs, invoke=invoke
        )
        for pid, execution in report.executions.items():
            if execution.state is not ExecutionState.SUCCESS:
                continue
            for ref in staged.get(pid, []):
                artifacts.append(ref)
                app.bus.emit(
                    EventType.ARTIFACT_CREATED,
                    run_id=run_id,
                    plugin_id=pid,
                    data=ref.model_dump(mode="json"),
                )
        return report

    def _plugin_identities(self, execution_plan: ExecutionPlan) -> dict[str, dict[str, object]]:
        return {
            pid: {
                "version": node.metadata.version,
                "implementation_sha256": node.implementation_sha256,
                "priority": node.metadata.priority,
                "required": pid in execution_plan.required,
                "deterministic": node.metadata.deterministic,
                "python_dependencies": {
                    d: distribution_version(d) for d in node.metadata.python_dependencies
                },
            }
            for pid, node in execution_plan.nodes.items()
        }

    def _cache_key(
        self,
        request: AnalysisRequest,
        profile_name: str,
        execution_plan: ExecutionPlan,
        decisions: Mapping[str, PolicyDecision],
    ) -> str:
        app = self.app
        root: Path = app.root
        return cache_key(
            {
                "target": request.target.sha256,
                "reference": request.reference.sha256 if request.reference else None,
                "mode": app.profiles[profile_name].mode.value,
                "profile": app.profiles[profile_name].model_dump(mode="json"),
                # ponytail: whole settings snapshot — over-invalidates (e.g. on a log-level
                # change) but can never reuse a result computed under different settings.
                "settings": app.settings.model_dump(mode="json"),
                "flags": app.flags.snapshot(),
                "policy": {pid: d.enabled for pid, d in sorted(decisions.items())},
                "pipeline": execution_plan.fingerprint(),
                "plugins": self._plugin_identities(execution_plan),
                "image_decoders": app.decoders.library_versions(),
                # Raw file bytes, not parsed YAML: a malformed config must fail only its
                # plugin at run time, exactly as it does with the cache disabled.
                "plugin_configs": {
                    pid: _file_digest(root / PLUGIN_CONFIG_DIR / f"{pid}.yaml")
                    for pid in execution_plan.order
                },
                "application": app.identity.application.version,
                "python": list(sys.version_info[:2]),
            }
        )


def _file_digest(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None
