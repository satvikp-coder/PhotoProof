"""Dependency-aware execution planning (a validated DAG with a deterministic order).

Graph-definition errors (unknown ``requires``, cycles, priority inversions, unprovided
input contracts, duplicate output producers) raise ``PlanningError`` for the whole
registry, whether or not the offending plugins are enabled.

Policy effects (a required plugin disabled, an unavailable dependency under the
``block`` policy, an unsatisfiable ``required_outputs``) produce a *blocked* plan
instead: nothing runs and the result is reported as ``BLOCKED``.

Order: Kahn's topological sort with a min-heap on ``(priority, discovery_index)``.
Because a dependency may never have a later priority than its dependent, the order
is non-decreasing in priority and ``stages`` are the priority barriers.
"""

import heapq
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import groupby

from photoproof.contracts.enums import AnalysisMode
from photoproof.contracts.exceptions import PlanningError
from photoproof.contracts.plugins import RUN_INPUTS
from photoproof.core.config.policy import PolicyDecision
from photoproof.core.config.validator import Profile, Settings
from photoproof.core.pipeline.registry import PluginRegistry, RegisteredPlugin
from photoproof.core.storage.store import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ExecutionPlan:
    order: tuple[str, ...]
    nodes: Mapping[str, RegisteredPlugin]
    required: frozenset[str]
    excluded: Mapping[str, str]  # plugin id -> why it is not in the plan
    blocked_reasons: tuple[str, ...] = ()
    stages: tuple[tuple[str, ...], ...] = field(default=())

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reasons)

    def graph(self) -> dict[str, dict[str, list[str]]]:
        return {
            pid: {
                "requires": sorted(self.nodes[pid].metadata.requires),
                "after": sorted(self.nodes[pid].metadata.after),
            }
            for pid in self.order
        }

    def fingerprint(self) -> str:
        """Identity of the computation: order, graph, versions and implementation digests."""
        return sha256_bytes(
            canonical_json(
                {
                    "order": list(self.order),
                    "graph": self.graph(),
                    "required": sorted(self.required),
                    "plugins": {
                        pid: [node.metadata.version, node.implementation_sha256]
                        for pid, node in self.nodes.items()
                    },
                }
            )
        )


def validate_graph(registry: PluginRegistry) -> None:
    """Reject structural problems in the plugin graph. See module docstring."""
    plugins = {p.id: p for p in registry}
    producers: dict[str, str] = {}
    for plugin in plugins.values():
        meta = plugin.metadata
        for output in meta.outputs:
            if output in producers:
                raise PlanningError(
                    f"output contract {output!r} is produced by both "
                    f"{producers[output]} and {meta.id}"
                )
            producers[output] = meta.id
    for plugin in plugins.values():
        meta = plugin.metadata
        for dep in meta.requires:
            if dep not in registry.known_ids:
                raise PlanningError(f"{meta.id}: required dependency {dep!r} does not exist")
        for dep in (*meta.requires, *meta.after):
            if dep in plugins and plugins[dep].metadata.priority > meta.priority:
                raise PlanningError(
                    f"{meta.id} (priority {meta.priority}) depends on {dep} "
                    f"(priority {plugins[dep].metadata.priority}); a dependency cannot run at a "
                    "later priority. Lower the dependency's priority or raise the dependent's."
                )
        if any(dep not in plugins for dep in meta.requires):
            continue  # an unavailable dependency's outputs are unknown; it will be skipped
        provided = set(RUN_INPUTS).union(*(plugins[d].metadata.outputs for d in meta.requires))
        for contract in meta.inputs:
            if contract not in provided:
                raise PlanningError(
                    f"{meta.id}: input contract {contract!r} is not a run input and is not "
                    "produced by any plugin listed in `requires`"
                )
    _reject_cycles(plugins)


def _reject_cycles(plugins: Mapping[str, RegisteredPlugin]) -> None:
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(plugins, white)

    def visit(pid: str, path: list[str]) -> None:
        color[pid] = grey
        meta = plugins[pid].metadata
        for dep in sorted((*meta.requires, *meta.after)):
            if dep not in plugins:
                continue
            if color[dep] == grey:
                cycle = [*path[path.index(dep) :], dep] if dep in path else [pid, dep]
                raise PlanningError(f"dependency cycle: {' -> '.join(cycle)}")
            if color[dep] == white:
                visit(dep, [*path, dep])
        color[pid] = black

    for pid in sorted(plugins):
        if color[pid] == white:
            visit(pid, [pid])


def plan(
    registry: PluginRegistry,
    decisions: Mapping[str, PolicyDecision],
    *,
    profile: Profile,
    settings: Settings,
) -> ExecutionPlan:
    validate_graph(registry)
    for pid in (*profile.plugins.enable, *profile.plugins.disable):
        if pid not in registry.known_ids:
            raise PlanningError(f"profile {profile.name!r} references unknown plugin {pid!r}")

    excluded: dict[str, str] = {
        u.plugin_id or u.package: f"unavailable: {u.reason}" for u in registry.unavailable
    }
    active: dict[str, RegisteredPlugin] = {}
    for plugin in sorted(registry, key=lambda p: p.discovery_index):
        decision = decisions[plugin.id]
        if decision.enabled:
            active[plugin.id] = plugin
        else:
            excluded[plugin.id] = f"disabled ({decision.source.value}: {decision.reason})"

    required = frozenset(p.id for p in registry if p.metadata.required) | set(
        profile.required_plugins
    )
    blocked: list[str] = []

    # Which active plugins will certainly be skipped because a `requires` link is missing?
    order = _topological_order(active)
    will_skip: dict[str, str] = {}
    for pid in order:
        for dep in active[pid].metadata.requires:
            if dep not in active or dep in will_skip:
                will_skip[pid] = (
                    f"requires {dep}, which is {'not planned' if dep not in active else 'skipped'}"
                )
                break
    if will_skip and settings.dependencies.on_unavailable_requirement == "block":
        blocked += [
            f"{pid} {reason} (dependencies.on_unavailable_requirement=block)"
            for pid, reason in will_skip.items()
        ]

    needs_reference = profile.mode is not AnalysisMode.COMPARISON
    for pid in sorted(required):
        if pid in active and needs_reference and "input.reference" in active[pid].metadata.inputs:
            blocked.append(
                f"required plugin {pid} needs a reference image, but profile "
                f"{profile.name!r} is single-image"
            )
        elif pid not in active:
            blocked.append(
                f"required plugin {pid} cannot run: {excluded.get(pid, 'not installed')}"
            )
        elif pid in will_skip:
            blocked.append(f"required plugin {pid} would be skipped: {will_skip[pid]}")

    producible = {
        out for pid, node in active.items() if pid not in will_skip for out in node.metadata.outputs
    }
    blocked += [
        f"profile {profile.name!r} requires output {out!r}, which no runnable plugin produces"
        for out in profile.required_outputs
        if out not in producible
    ]

    stages = tuple(
        tuple(group) for _, group in groupby(order, key=lambda pid: active[pid].metadata.priority)
    )
    return ExecutionPlan(
        order=tuple(order),
        nodes=active,
        required=required,
        excluded=excluded,
        blocked_reasons=tuple(blocked),
        stages=stages,
    )


def _topological_order(nodes: Mapping[str, RegisteredPlugin]) -> list[str]:
    indegree = dict.fromkeys(nodes, 0)
    dependents: dict[str, list[str]] = {pid: [] for pid in nodes}
    for pid, node in nodes.items():
        for dep in (*node.metadata.requires, *node.metadata.after):
            if dep in nodes:
                indegree[pid] += 1
                dependents[dep].append(pid)

    def key(pid: str) -> tuple[int, int, str]:
        return nodes[pid].metadata.priority, nodes[pid].discovery_index, pid

    heap = [key(pid) for pid, degree in indegree.items() if degree == 0]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        *_, pid = heapq.heappop(heap)
        order.append(pid)
        for child in dependents[pid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(heap, key(child))
    if len(order) != len(nodes):  # unreachable after validate_graph; defensive
        raise PlanningError("dependency cycle among enabled plugins")
    return order
