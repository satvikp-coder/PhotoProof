from photoproof.core.pipeline.manifest import build_manifest


def _manifest(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = dict(
        run_id="r1",
        created_at="t0",
        finished_at="t1",
        application={"version": "0.1.0"},
        status="COMPLETED",
        mode="single",
        profile="standard",
        inputs=[{"role": "target", "sha256": "0" * 64, "size": 1}],
        pipeline_fingerprint="p",
        cache={"key": None, "hit": False},
        effective_settings={"a": 1},
        feature_flags={"f": True},
        policy={},
        blacklist_override=False,
        plugins={},
        dependency_graph={},
        execution_order=[],
        executions={
            "p.a": {
                "state": "SUCCESS",
                "failure_category": None,
                "started_at": "t0",
                "message": "x",
            }
        },
        blocked_reasons=[],
        random_seed=0,
        artifacts=[],
        outputs={},
        software={"python": "3.12.1"},
    )
    fields.update(overrides)
    return build_manifest(**fields)  # type: ignore[arg-type]


def test_fingerprint_ignores_incidental_fields() -> None:
    base = _manifest()["computational_fingerprint"]
    assert (
        _manifest(run_id="r2", created_at="x", finished_at="y")["computational_fingerprint"] == base
    )
    other_exec = {
        "p.a": {
            "state": "SUCCESS",
            "failure_category": None,
            "started_at": "t9",
            "message": "different",
        }
    }
    assert (
        _manifest(executions=other_exec, software={"python": "3.12.9"})["computational_fingerprint"]
        == base
    )


def test_fingerprint_tracks_computational_state() -> None:
    base = _manifest()["computational_fingerprint"]
    assert _manifest(random_seed=1)["computational_fingerprint"] != base
    assert _manifest(feature_flags={"f": False})["computational_fingerprint"] != base
    failed = {"p.a": {"state": "FAILED", "failure_category": "exception"}}
    assert _manifest(executions=failed)["computational_fingerprint"] != base


def test_manifest_records_required_fields() -> None:
    manifest = _manifest()
    for key in (
        "run_id",
        "inputs",
        "mode",
        "application",
        "pipeline_fingerprint",
        "effective_settings",
        "feature_flags",
        "plugins",
        "dependency_graph",
        "execution_order",
        "executions",
        "software",
        "random_seed",
        "artifacts",
    ):
        assert key in manifest
