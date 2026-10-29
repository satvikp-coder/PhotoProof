import time

from photoproof.contracts.exceptions import NotApplicableError
from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    behavior = getattr(ctx.config, "behavior", "ok")
    if behavior == "raise":
        raise RuntimeError("simulated plugin failure")
    if behavior == "invalid_output":
        return {"undeclared.output": 1}
    if behavior == "not_json":
        return {"demo.configurable_result": object()}
    if behavior == "sleep":
        time.sleep(5)
    if behavior == "interrupt":
        raise KeyboardInterrupt
    if behavior == "not_applicable":
        raise NotApplicableError("simulated: input not applicable")
    return {"demo.configurable_result": {"behavior": behavior, "seed": ctx.seed}}
