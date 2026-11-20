"""Real residual and variance algorithms consume the persisted upstream artifact."""

from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.core.controller import AnalysisController
from photoproof.plugins.noise.variance_map.algorithm import analyze_noise_variance


def test_real_residual_artifact_drives_variance(make_app, tmp_path, monkeypatch):
    from photoproof.plugins.fusion.normalize import processor
    from photoproof.plugins.fusion.normalize.native import read_native_anomaly

    observed = []
    original_run = processor.run

    def inspect_actual_context(ctx):
        observed.append(
            read_native_anomaly(
                ctx,
                "noise.consistency_map",
                geometry=ctx.inputs["image.geometry"],
                strength_class="medium",
                z0=2.5,
                k=1.0,
            )
        )
        return original_run(ctx)

    monkeypatch.setattr(processor, "run", inspect_actual_context)
    app = make_app("photoproof.plugins")
    rng = np.random.default_rng(42)
    pixels = np.clip(rng.normal(120, 4, (128, 128, 3)), 0, 255).astype(np.uint8)
    path = tmp_path / "synthetic-noise.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    target = app.ingestion().ingest_path(path, role="target")[0]
    result = AnalysisController(app).analyze(AnalysisRequest(target=target, use_cache=False))
    states = {p.plugin_id: p.state for p in result.plugins}
    assert states["noise.residual"] == "SUCCESS"
    assert states["noise.variance_map"] == "SUCCESS"
    residual_ref = result.outputs["noise.residual"]["artifact"]
    residual = decode_npy(app.storage.blob_path("artifacts", residual_ref["sha256"]).read_bytes())
    expected = analyze_noise_variance(residual, pixels)
    for key in ("sigma_map", "consistency_map"):
        ref = result.outputs[f"noise.{key}"]["artifact"]
        actual = decode_npy(app.storage.blob_path("artifacts", ref["sha256"]).read_bytes())
        np.testing.assert_array_equal(actual, expected[key])
    summary = result.outputs["noise.variance_summary"]
    assert summary["format_version"] == 2
    assert summary["source_sha256"] == target.sha256
    assert summary["coordinate_space"] == "decoded_pixels"
    validity_ref = summary["valid_mask_artifact"]["artifact"]
    actual_validity = decode_npy(
        app.storage.blob_path("artifacts", validity_ref["sha256"]).read_bytes()
    )
    np.testing.assert_array_equal(actual_validity, expected["valid_mask"])
    assert "values" not in summary["sigma_map"] and "values" not in summary["consistency_map"]
    assert "valid_mask" not in summary["sigma_map"]
    assert summary["grid_shape"] == list(expected["valid_mask"].shape)
    assert len(observed) == 1 and observed[0] is not None
    np.testing.assert_array_equal(observed[0].layer.valid_mask, expected["valid_mask"])
    assert observed[0].source_sha256 == target.sha256
    assert observed[0].layer.calibration == "uncalibrated"
    assert observed[0].sampling_grid.image_size == (128, 128)
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert residual_ref["sha256"] in {record["sha256"] for record in manifest["artifacts"]}
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
