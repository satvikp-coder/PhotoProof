"""Launch the actual launcher/web command in a disposable source/runtime copy.

Run with a supported Python; the copied launcher installs its locked environment.
Stop with Ctrl+C. No real .venv, evidence, settings or runtime directories are used.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18501)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="photoproof38-preview-") as directory:
        root = Path(directory)
        for name in (
            "main.py",
            "app.toml",
            "settings.yaml",
            "pyproject.toml",
            "requirements.lock",
            "README.md",
            "LICENSE",
        ):
            shutil.copy(source / name, root / name)
        for name in ("src", "WebKit", "configs"):
            shutil.copytree(
                source / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        settings = root / "settings.yaml"
        settings.write_text(settings.read_text().replace("port: 8501", f"port: {args.port}"))
        print(f"Disposable preview: http://127.0.0.1:{args.port}", flush=True)
        result = subprocess.run(
            [sys.executable, str(root / "main.py"), "web"], cwd=root, check=False
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
