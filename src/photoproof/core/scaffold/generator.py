"""Generate a plugin scaffold from templates."""

from __future__ import annotations

import string
from pathlib import Path

from . import (
    _DEFAULT_PRIORITY,
    ScaffoldError,
    _default_inputs,
    _default_outputs,
    _default_requires,
    plugin_root,
    validate_plugin_id,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> string.Template:
    return string.Template((TEMPLATES_DIR / name).read_text(encoding="utf-8"))


def _to_snake(name: str) -> str:
    """Convert a plugin name component to a safe Python identifier."""
    return name.replace("-", "_").replace(".", "_")


def generate(
    plugin_id: str,
    *,
    base: Path,
    priority: int | None = None,
    required: bool = False,
    enabled_default: bool = True,
    description: str | None = None,
) -> Path:
    """Generate a plugin skeleton under ``src/photoproof/plugins/<family>/<name>/``.

    Returns the plugin directory path.  Raises :exc:`ScaffoldError` if the id is
    invalid or the target directory already exists.
    """
    family, name = validate_plugin_id(plugin_id)
    target = plugin_root(family, name, base)
    if target.exists():
        raise ScaffoldError(f"plugin directory already exists: {target}")

    target.mkdir(parents=True)

    if priority is None:
        priority = _DEFAULT_PRIORITY.get(family, 50)

    desc = description or f"{name} detector plugin."
    inputs = _default_inputs(family)
    outputs = _default_outputs(family)
    requires = _default_requires(family)
    snake = _to_snake(name)
    name_display = name.replace("-", " ").title().replace("_", " ")
    plugin_display = f"{family}.{name}"

    context = {
        "plugin_id": plugin_id,
        "plugin_display": plugin_display,
        "family": family,
        "name": name,
        "snake_name": snake,
        "name_display": name_display,
        "description": desc,
        "priority": str(priority),
        "required": "True" if required else "False",
        "enabled_default": "True" if enabled_default else "False",
        "requires": repr(tuple(requires)),
        "inputs": repr(tuple(inputs)),
        "outputs": repr(tuple(outputs)),
        "first_input": inputs[0] if inputs else "image.decoded",
        "first_output": outputs[0] if outputs else f"{family}.result",
        "second_output": outputs[1] if len(outputs) > 1 else f"{family}.result_stats",
    }

    files = {
        "__init__.py": _load_template("__init__.py.template"),
        "algorithm.py": _load_template("algorithm.py.template"),
        "processor.py": _load_template("processor.py.template"),
        "schema.py": _load_template("schema.py.template"),
    }

    for filename, template in files.items():
        rendered = template.safe_substitute(context)
        (target / filename).write_text(rendered, encoding="utf-8")

    # Test file
    test_template = _load_template("test_$family_$name.py.template")
    test_path = base / "tests" / "unit" / "plugins" / f"test_{family}_{name}.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(test_template.safe_substitute(context), encoding="utf-8")

    # Docs page
    doc_template = _load_template("$family-$name.md.template")
    doc_path = base / "docs" / "plugins" / f"{family}-{name}.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(doc_template.safe_substitute(context), encoding="utf-8")

    return target
