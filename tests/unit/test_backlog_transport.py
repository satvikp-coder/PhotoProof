"""GitHub CLI payloads retain Unicode on non-UTF-8 Windows locales."""

import importlib.util
import locale
import subprocess
import sys
from pathlib import Path


def test_backlog_cli_roundtrips_utf8_independent_of_locale(monkeypatch):
    source = Path(__file__).resolve().parents[2] / ".github/scripts/backlog.py"
    spec = importlib.util.spec_from_file_location("backlog_transport", source)
    assert spec is not None and spec.loader is not None
    backlog = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, backlog)
    spec.loader.exec_module(backlog)
    real_run = subprocess.run
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")
    payload = "M1 \u00b7 Secure ingestion \u2194 evidence"
    child = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"

    def local_cli(command, **kwargs):
        assert command == ["gh", "api", "example"]
        return real_run([sys.executable, "-c", child], **kwargs)

    monkeypatch.setattr(backlog.subprocess, "run", local_cli)
    assert backlog.gh("api", "example", input_text=payload) == payload
