from pathlib import Path

import pytest
from helpers import meta, registry_of, update_settings

from photoproof.contracts.exceptions import PlanningError
from photoproof.core.config.loader import load_identity, load_settings
from photoproof.core.config.policy import DecisionSource, PolicyDecision
from photoproof.core.config.validator import Profile
from photoproof.core.pipeline.planner import plan
from photoproof.core.pipeline.registry import PluginRegistry


def _plan(
    root: Path,
    registry: PluginRegistry,
    *,
    disabled: set[str] = frozenset(),  # type: ignore[assignment]
    profile: dict | None = None,
):  # type: ignore[no-untyped-def,type-arg]
    settings = load_settings(root, load_identity(root), environ={})
    decisions = {
        p.id: PolicyDecision(p.id, p.id not in disabled, DecisionSource.DEFAULT, "test")
        for p in registry
    }
    prof = Profile.model_validate(
        {"name": "t", "description": "t", "mode": "single", **(profile or {})}
    )
    return plan(registry, decisions, profile=prof, settings=settings)


def test_ascending_priority_and_stable_ties(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(
        tmp_path,
        meta("p.late", priority=20),
        meta("p.b", priority=10),
        meta("p.a", priority=10),
        meta("p.init", priority=0),
    )
    result = _plan(app_root, registry)
    assert result.order == ("p.init", "p.b", "p.a", "p.late")  # ties keep discovery order
    assert result.stages == (("p.init",), ("p.b", "p.a"), ("p.late",))


def test_dependency_ordering(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(
        tmp_path,
        meta("p.consumer", priority=10, requires=["p.producer"], inputs=["p.producer.out"]),
        meta("p.producer", priority=10),
        meta("p.observer", priority=10, after=["p.consumer"]),
    )
    assert _plan(app_root, registry).order == ("p.producer", "p.consumer", "p.observer")


def test_circular_dependency_rejected(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(tmp_path, meta("p.a", after=["p.b"]), meta("p.b", after=["p.a"]))
    with pytest.raises(PlanningError, match="cycle"):
        _plan(app_root, registry)


def test_missing_required_dependency_rejected(app_root: Path, tmp_path: Path) -> None:
    with pytest.raises(PlanningError, match="does not exist"):
        _plan(app_root, registry_of(tmp_path, meta("p.a", requires=["p.ghost"])))


def test_missing_ordering_dependency_is_ignored(app_root: Path, tmp_path: Path) -> None:
    assert _plan(app_root, registry_of(tmp_path, meta("p.a", after=["p.ghost"]))).order == ("p.a",)


def test_priority_inversion_rejected(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(
        tmp_path, meta("p.a", priority=0, requires=["p.b"]), meta("p.b", priority=5)
    )
    with pytest.raises(PlanningError, match="later priority"):
        _plan(app_root, registry)


def test_unprovided_input_contract_rejected(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(tmp_path, meta("p.a", inputs=["p.b.out"]), meta("p.b", priority=0))
    with pytest.raises(PlanningError, match="not produced by any plugin listed in `requires`"):
        _plan(app_root, registry)


def test_duplicate_output_producers_rejected(app_root: Path, tmp_path: Path) -> None:
    registry = registry_of(tmp_path, meta("p.a", outputs=["x.y"]), meta("p.b", outputs=["x.y"]))
    with pytest.raises(PlanningError, match="produced by both"):
        _plan(app_root, registry)


def test_disabled_plugin_excluded(app_root: Path, tmp_path: Path) -> None:
    result = _plan(app_root, registry_of(tmp_path, meta("p.a"), meta("p.b")), disabled={"p.b"})
    assert result.order == ("p.a",) and "disabled" in result.excluded["p.b"]


def test_required_plugin_disabled_blocks(app_root: Path, tmp_path: Path) -> None:
    result = _plan(app_root, registry_of(tmp_path, meta("p.a", required=True)), disabled={"p.a"})
    assert result.blocked and "required plugin p.a" in result.blocked_reasons[0]


def test_profile_required_plugin_whose_dependency_is_disabled_blocks(
    app_root: Path, tmp_path: Path
) -> None:
    registry = registry_of(tmp_path, meta("p.a", requires=["p.b"]), meta("p.b", priority=0))
    result = _plan(app_root, registry, disabled={"p.b"}, profile={"required_plugins": ["p.a"]})
    assert result.blocked and "would be skipped" in result.blocked_reasons[0]


def test_unavailable_requirement_block_policy(app_root: Path, tmp_path: Path) -> None:
    update_settings(app_root, dependencies={"on_unavailable_requirement": "block"})
    registry = registry_of(tmp_path, meta("p.a", requires=["p.b"]), meta("p.b", priority=0))
    assert _plan(app_root, registry, disabled={"p.b"}).blocked


def test_required_output_unsatisfied_blocks(app_root: Path, tmp_path: Path) -> None:
    result = _plan(
        app_root, registry_of(tmp_path, meta("p.a")), profile={"required_outputs": ["z.z"]}
    )
    assert result.blocked and "z.z" in result.blocked_reasons[0]


def test_profile_referencing_unknown_plugin(app_root: Path, tmp_path: Path) -> None:
    with pytest.raises(PlanningError, match="unknown plugin"):
        _plan(
            app_root, registry_of(tmp_path, meta("p.a")), profile={"plugins": {"disable": ["p.zz"]}}
        )


def test_fingerprint_is_stable_and_sensitive(app_root: Path, tmp_path: Path) -> None:
    one = _plan(app_root, registry_of(tmp_path, meta("p.a"))).fingerprint()
    assert one == _plan(app_root, registry_of(tmp_path, meta("p.a"))).fingerprint()
    assert one != _plan(app_root, registry_of(tmp_path, meta("p.a", version="1.0.1"))).fingerprint()
