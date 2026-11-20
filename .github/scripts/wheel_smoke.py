"""Install a wheel in a fresh external environment and exercise actual CLI analysis."""

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import venv
import zlib
from pathlib import Path


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + bytes([90, 120, 150]) * 32) * 32))
        + chunk(b"IEND", b"")
    )


def smoke(wheel: Path, lock: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="photoproof-wheel-") as directory:
        outside = Path(directory).resolve()
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("PHOTOPROOF_")
        }
        environment.update(
            PYTHONUTF8="1",
            PIP_DISABLE_PIP_VERSION_CHECK="1",
            PIP_CACHE_DIR=str(outside / "pip-cache"),
        )
        target = outside / "venv"
        venv.EnvBuilder(with_pip=True).create(target)
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def run(
            arguments: list[str], *, allowed: tuple[int, ...] = (0,)
        ) -> subprocess.CompletedProcess[str]:
            result = subprocess.run(
                [str(python), *arguments],
                cwd=outside,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=180,
            )
            if result.returncode not in allowed:
                raise RuntimeError(
                    f"wheel smoke command {arguments} failed ({result.returncode}): "
                    f"{result.stdout}\n{result.stderr}"
                )
            return result

        run(
            [
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--only-binary",
                ":all:",
                "-r",
                str(lock.resolve()),
            ]
        )
        run(["-m", "pip", "install", "--no-deps", str(wheel.resolve())])
        imported = Path(run(["-c", "import photoproof; print(photoproof.__file__)"]).stdout.strip())
        if target not in imported.resolve().parents:
            raise RuntimeError(f"wheel smoke imported outside the fresh environment: {imported}")
        home = outside / "runtime-home"
        arguments = ["-m", "photoproof", "--root", str(home)]
        # status must work before init, using read-only packaged defaults.
        run([*arguments, "status"])
        run([*arguments, "init"])
        before = (home / "settings.yaml").read_bytes()
        again = run([*arguments, "init"])
        if "created " in again.stdout or (home / "settings.yaml").read_bytes() != before:
            raise RuntimeError("repeat init modified existing configuration")
        image = outside / "generated.png"
        original = _png()
        image.write_bytes(original)
        analysis = run([*arguments, "analyze", str(image), "--no-cache", "--json"], allowed=(0, 7))
        result = json.loads(analysis.stdout)
        if result["status"] not in {"COMPLETED", "INCOMPLETE"}:
            raise RuntimeError(f"installed analysis did not execute: {result['status']}")
        states = {item["plugin_id"]: item["state"] for item in result["plugins"]}
        if states.get("support.preview") != "SUCCESS":
            raise RuntimeError(f"installed decoding/pixel consumer failed: {result['plugins']}")
        failures = [item for item in result["plugins"] if item["state"] == "FAILED"]
        digest = hashlib.sha256(original).hexdigest()
        stored = home / "ARE" / "objects" / digest[:2] / digest
        if not stored.is_file() or stored.read_bytes() != original:
            raise RuntimeError("installed analysis did not preserve immutable stored evidence")
        manifest = Path(result["manifest_path"])
        if not manifest.is_absolute():
            manifest = home / manifest
        # The CLI returns the storage-relative manifest path on supported releases.
        if not manifest.is_file():
            matches = list((home / "ARE" / "manifests").glob(f"{result['run_id']}.json"))
            if len(matches) != 1:
                raise RuntimeError("installed analysis did not persist its actual manifest")
            manifest = matches[0]
        if home not in manifest.resolve().parents or image.read_bytes() != original:
            raise RuntimeError("installed analysis escaped its runtime home or modified evidence")
        return {
            "imported_from_fresh_environment": True,
            "status_before_init": "passed",
            "init_preserves_configuration": True,
            "analysis_status": result["status"],
            "analysis_exit_code": analysis.returncode,
            "failed_plugins": failures,
            "pixel_consumer": "support.preview:SUCCESS",
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "manifest_recorded": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    wheels = list(args.wheel_dir.glob("photoproof-*.whl"))
    if len(wheels) != 1:
        parser.error("wheel directory must contain exactly one PhotoProof wheel")
    result = smoke(wheels[0], args.lock)
    encoded = json.dumps(result, indent=2)
    print(encoded)
    if args.report is not None:
        args.report.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
