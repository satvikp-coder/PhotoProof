"""Unsupported or absent outputs must never become clean-score evidence."""

import logging

import numpy as np
import pytest
from pydantic import ValidationError

from photoproof.contracts.exceptions import InputRejectedError
from photoproof.contracts.plugins import PluginContext
from photoproof.plugins.fusion.normalize.processor import run
from photoproof.plugins.fusion.normalize.schema import Config


@pytest.mark.parametrize("value", [None, {}, {"artifact": {"sha256": "a" * 64}}, "failed", []])
def test_unusable_output_has_no_layer(value):
    result = _run(value)
    assert not result["fusion.layers"]
    assert "compression.ela_map" in result["fusion.unavailable"]


def _run(value):
    return run(
        PluginContext(
            run_id="availability",
            plugin_id="fusion.normalize",
            config={},
            seed=0,
            inputs={"compression.ela_map": value},
            logger=logging.getLogger(__name__),
            write_artifact=lambda *args: None,
        )
    )


def test_serialized_numeric_map_retains_usable_evidence():
    result = _run([[0.0, 1.0], [2.0, 4.0]])
    assert len(result["fusion.layers"]) == 1
    assert np.asarray(result["fusion.layers"][0]["valid_mask"]).all()
    assert np.asarray(result["fusion.layers"][0]["map"]).max() > 0


def test_verified_binary_mask_retains_localization():
    ctx = PluginContext(
        run_id="mask",
        plugin_id="fusion.normalize",
        config={},
        seed=0,
        inputs={"copymove.mask": [[0.0, 0.0], [0.0, 1.0]]},
        logger=logging.getLogger(__name__),
        write_artifact=lambda *args: None,
    )
    layer = run(ctx)["fusion.layers"][0]
    assert layer["map"] == [[0.0, 0.0], [0.0, 1.0]]
    assert layer["strength_class"] == "strong"


@pytest.mark.parametrize("value", [float("nan"), [[0.0, float("inf")]]])
def test_nonfinite_evidence_fails_visibly(value):
    with pytest.raises(InputRejectedError, match="nonfinite"):
        _run(value)


def test_configured_mapping_is_applied():
    ctx = PluginContext(
        run_id="configured",
        plugin_id="fusion.normalize",
        seed=0,
        inputs={"compression.ela_map": [[0.0, 1.0], [2.0, 4.0]]},
        config=Config(mappings={"compression.ela_map": {"method": "identity"}}),
        logger=logging.getLogger(__name__),
        write_artifact=lambda *args: None,
    )
    with pytest.raises(InputRejectedError, match="unit-interval"):
        run(ctx)


@pytest.mark.parametrize("mapping", [{"unknown": {}}, {"compression.ela_map": {"k": 0}}])
def test_invalid_mapping_rejected(mapping):
    with pytest.raises(ValidationError):
        Config.model_validate({"mappings": mapping})
