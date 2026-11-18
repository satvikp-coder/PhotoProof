"""The public artifact capability accepts declared inputs and explicit byte ceilings."""

import logging

import pytest

from photoproof.contracts.exceptions import PolicyError
from photoproof.contracts.plugins import PluginContext


def _context(**kwargs):
    return PluginContext(
        run_id="artifact",
        plugin_id="consumer.probe",
        inputs={"producer.array": {}},
        config={},
        seed=0,
        write_artifact=lambda *args: None,
        logger=logging.getLogger(__name__),
        **kwargs,
    )


def test_declared_input_uses_bounded_reader():
    calls = []

    def read(name, limit, selector):
        calls.append((name, limit, selector))
        return b"verified-array"

    ctx = _context(read_artifact=read)
    assert ctx.artifact("producer.array", max_bytes=64) == b"verified-array"
    assert calls == [("producer.array", 64, ())]


def test_undeclared_input_never_calls_reader():
    calls = []
    ctx = _context(read_artifact=lambda *args: calls.append(args))
    with pytest.raises(PolicyError, match="did not declare"):
        ctx.artifact("private.other", max_bytes=64)
    assert not calls


def test_legacy_context_has_no_implicit_storage_access():
    with pytest.raises(PolicyError, match="not available"):
        _context().artifact("producer.array")
