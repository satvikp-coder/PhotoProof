from photoproof.contracts.exceptions import NotApplicableError
from photoproof.contracts.plugins import PluginContext


def run(ctx: PluginContext) -> dict[str, object]:
    raise NotApplicableError("fixture detector does not apply")
