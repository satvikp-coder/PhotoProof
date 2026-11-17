"""Reject malformed automation documents before they disable GitHub workflows."""

from pathlib import Path

import pytest
from ruamel.yaml import YAML

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_workflow_yaml_parses(path: Path) -> None:
    document = YAML(typ="safe").load(path)
    assert isinstance(document, dict)
    assert document.get("on") and document.get("jobs")
