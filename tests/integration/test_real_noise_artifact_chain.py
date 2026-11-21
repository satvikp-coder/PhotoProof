"""Real residual and variance algorithms consume the persisted upstream artifact."""

from pathlib import Path

import numpy as np
from PIL import Image

from photoproof.contracts.analysis import AnalysisRequest
from photoproof.contracts.arrays import decode_npy
from photoproof.core.controller import AnalysisController
from photoproof.plugins.noise.variance_map.algorithm import analyze_noise_variance


def test_real_residual_artifact_drives_variance(make_app, tmp_path):
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
    np.testing.assert_array_equal(summary["sigma_map"]["valid_mask"], expected["valid_mask"])
    manifest = app.storage.read_json("manifests", Path(result.manifest_path).name)
    assert residual_ref["sha256"] in {record["sha256"] for record in manifest["artifacts"]}
    assert app.storage.blob_path("objects", target.sha256).read_bytes() == original
