#!/usr/bin/env python3
"""PhotoProof universal launcher (standard library only).

Prepares a reproducible environment and starts the application:

    python main.py                  start (status shell)
    python main.py --doctor         diagnose; change nothing
    python main.py --repair         controlled repair (may rebuild .venv); add --dry-run to preview
    python main.py --force-setup    reinstall locked dependencies
    python main.py --ignore-blacklist <command>   audited administrative bypass
    python main.py --version        print the version from app.toml
    python main.py <command> ...    forward a command to the CLI (e.g. plugins, analyze IMG)

Launcher flags must come before the command; everything from the command onwards is
passed to the CLI unchanged (``python main.py repair --dry-run``).

Requires an already-installed compatible Python interpreter; it never installs Python,
never uses administrator privileges, and never downloads anything except the
hash-verified packages in requirements.lock.

Exit codes: 0 ok · 2 usage · 3 unsupported environment · 4 identity/config invalid ·
5 dependency installation failed · 6 preflight/health failure · others from the CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Identity = dict[str, Any]  # parsed app.toml

EXIT_OK, EXIT_USAGE, EXIT_UNSUPPORTED, EXIT_IDENTITY, EXIT_DEPS, EXIT_PREFLIGHT = 0, 2, 3, 4, 5, 6

# Bootstrap paths: fixed because they are needed before any configuration is read.
ROOT = Path(__file__).resolve().parent
APP_FILE = "app.toml"
LOCKFILE = "requirements.lock"
VENV_DIR = ".venv"
STATE_FILE = ".photoproof-env.json"  # inside .venv
_PROJECT_NAME = "photoproof"

_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "armv8": "arm64",
}


class LauncherError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Report:
    """Collected launcher findings: (status, name, detail)."""

    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, status: str, name: str, detail: str) -> None:
        self.rows.append((status, name, detail))

    def render(self) -> str:
        marks = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
        return "\n".join(f"  {marks[s]} {n:<20} {d}" for s, n, d in self.rows)


# --------------------------------------------------------------------- identity


def load_identity(root: Path) -> Identity:
    if sys.version_info < (3, 11):  # tomllib is required to read app.toml
        raise LauncherError(
            f"Python {platform.python_version()} is too old; PhotoProof needs Python >= 3.11",
            EXIT_UNSUPPORTED,
        )
    import tomllib

    path = root / APP_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LauncherError(
            f"{path} not found; run main.py from a PhotoProof checkout", EXIT_IDENTITY
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise LauncherError(f"{path} is malformed: {error}", EXIT_IDENTITY) from error
    try:
        app = data["application"]
        name, package, version = app["name"], app["package"], app["version"]
        data["python"]["minimum"], data["python"]["maximum"]
        data["platform"]["operating_systems"], data["platform"]["architectures"]
        data["hardware"]
    except (KeyError, TypeError) as error:
        raise LauncherError(f"{path}: missing required key {error}", EXIT_IDENTITY) from error
    if name.lower() != package:
        raise LauncherError(
            f"{path}: application.name.lower() ({name.lower()!r}) must equal "
            f"application.package ({package!r})",
            EXIT_IDENTITY,
        )
    if not (root / "src" / package / "__init__.py").is_file():
        raise LauncherError(f"package directory src/{package}/ not found", EXIT_IDENTITY)
    if not isinstance(version, str) or version.count(".") != 2:
        raise LauncherError(f"{path}: version {version!r} is not MAJOR.MINOR.PATCH", EXIT_IDENTITY)
    return data


# ----------------------------------------------------------------- environment


def _minor(text: str) -> tuple[int, int]:
    major, minor = text.split(".")[:2]
    return int(major), int(minor)


def check_python(identity: Identity, version_info: tuple[int, ...] | None = None) -> str:
    current = tuple((version_info or sys.version_info)[:2])
    low, high = _minor(identity["python"]["minimum"]), _minor(identity["python"]["maximum"])
    text = ".".join(map(str, current))
    if not low <= current <= high:
        raise LauncherError(
            f"Python {text} is not supported; use Python {identity['python']['minimum']}-"
            f"{identity['python']['maximum']} (e.g. `python3.12 main.py`)",
            EXIT_UNSUPPORTED,
        )
    return text


def check_platform(
    identity: Identity, system: str | None = None, machine: str | None = None
) -> tuple[str, str]:
    system = system or platform.system()
    arch = _ARCH_ALIASES.get((machine or platform.machine()).lower(), machine or platform.machine())
    if system not in identity["platform"]["operating_systems"]:
        raise LauncherError(f"operating system {system!r} is not supported", EXIT_UNSUPPORTED)
    if arch not in identity["platform"]["architectures"]:
        raise LauncherError(f"CPU architecture {arch!r} is not supported", EXIT_UNSUPPORTED)
    return system, arch


def total_memory_mb() -> int | None:
    """Total physical memory, or None when it cannot be determined reliably."""
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
        elif sys.platform == "darwin":
            out = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout
            return int(out.strip()) // (1024 * 1024)
        elif sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys) // (1024 * 1024)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def check_hardware(
    identity: Identity,
    root: Path,
    report: Report,
    *,
    cpu_count: int | None = None,
    memory_mb: int | None = -1,
    free_mb: int | None = None,
) -> None:
    hw = identity["hardware"]
    cpus = os.cpu_count() if cpu_count is None else cpu_count
    if cpus is None:
        report.add("warn", "cpu", "core count unknown")
    elif cpus < hw["min_cpu_cores"]:
        raise LauncherError(
            f"{cpus} CPU cores; at least {hw['min_cpu_cores']} required", EXIT_UNSUPPORTED
        )
    else:
        status = "ok" if cpus >= hw["recommended_cpu_cores"] else "warn"
        report.add(status, "cpu", f"{cpus} cores (recommended {hw['recommended_cpu_cores']})")

    memory = total_memory_mb() if memory_mb == -1 else memory_mb
    if memory is None:
        report.add("warn", "memory", "unknown on this platform")
    elif memory < hw["min_memory_mb"]:
        raise LauncherError(
            f"{memory} MB memory; at least {hw['min_memory_mb']} MB required", EXIT_UNSUPPORTED
        )
    else:
        status = "ok" if memory >= hw["recommended_memory_mb"] else "warn"
        report.add(status, "memory", f"{memory} MB (recommended {hw['recommended_memory_mb']} MB)")

    free = shutil.disk_usage(root).free // (1024 * 1024) if free_mb is None else free_mb
    if free < hw["min_free_disk_mb"]:
        raise LauncherError(
            f"{free} MB free disk; {hw['min_free_disk_mb']} MB required", EXIT_UNSUPPORTED
        )
    report.add("ok", "disk", f"{free} MB free")
    report.add("info", "gpu", f"{hw.get('gpu', 'optional')} (not used by the base architecture)")


# ---------------------------------------------------------- virtual environment


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_version(venv: Path) -> str | None:
    python = venv_python(venv)
    if not python.is_file():
        return None
    try:
        out = subprocess.run(
            [str(python), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip()


def create_venv(venv: Path) -> None:
    import venv as venv_module

    print(f"Creating virtual environment in {venv} …", file=sys.stderr)
    venv_module.EnvBuilder(with_pip=True, clear=False).create(venv)


def verify_lockfile(root: Path, name: str = LOCKFILE) -> str:
    """Every requirement must be pinned with ``==`` and carry at least one hash."""
    path = root / name
    if not path.is_file():
        raise LauncherError(f"{name} is missing; restore it from Git", EXIT_DEPS)
    text = path.read_text(encoding="utf-8")
    requirements = [
        r.strip()
        for r in text.replace("\\\n", " ").splitlines()
        if r.strip() and not r.lstrip().startswith("#")
    ]
    if not requirements:
        raise LauncherError(f"{name} lists no requirements", EXIT_DEPS)
    for requirement in requirements:
        if "==" not in requirement.split()[0] or "--hash=sha256:" not in requirement:
            raise LauncherError(
                f"{name}: {requirement.split()[0]} is not pinned with a hash", EXIT_DEPS
            )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment_fingerprint(
    root: Path, lock_sha: str, python_version: str, app_version: str
) -> str:
    pyproject = hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest()
    return hashlib.sha256(
        f"{lock_sha}|{pyproject}|{python_version}|{app_version}".encode()
    ).hexdigest()


def read_state(venv: Path) -> dict[str, Any]:
    try:
        state: dict[str, Any] = json.loads((venv / STATE_FILE).read_text(encoding="utf-8"))
        return state
    except (OSError, ValueError):
        return {}


def _pip(python: Path, *args: str) -> None:
    command = [str(python), "-m", "pip", "--disable-pip-version-check", "--no-input", *args]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise LauncherError(
            "dependency installation failed: "
            + " ".join(command[3:])
            + "\nIf a package has no wheel for this platform, see "
            "docs/operations/troubleshooting.md",
            EXIT_DEPS,
        )


def unhide_pth_files(venv: Path) -> list[str]:
    """Clear the macOS UF_HIDDEN flag from ``*.pth`` files in the venv.

    Python >= 3.13 ignores hidden ``.pth`` files, which silently breaks the editable
    install. Some macOS setups flag new files inside dot-directories as hidden.
    """
    hidden = getattr(__import__("stat"), "UF_HIDDEN", 0)
    if sys.platform != "darwin" or not hidden:
        return []
    cleared = []
    for pth in venv.glob("lib/python3*/site-packages/*.pth"):
        flags = pth.stat().st_flags
        if flags & hidden:
            os.chflags(pth, flags & ~hidden)
            cleared.append(pth.name)
    return cleared


def installed_version(python: Path, package: str) -> str | None:
    """Version of the *importable* package (not just its metadata), or None."""
    code = f"import importlib.metadata as m, {package}; print(m.version({package!r}))"
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def ensure_environment(
    root: Path, identity: Identity, *, force: bool, rebuild: bool, report: Report
) -> Path:
    venv = root / VENV_DIR
    package, version = identity["application"]["package"], identity["application"]["version"]
    lock_sha = verify_lockfile(root)
    report.add("ok", "lockfile", f"{LOCKFILE} pinned and hashed ({lock_sha[:12]}…)")

    existing = venv_version(venv)
    if existing is not None:
        try:
            check_python(identity, tuple(int(p) for p in existing.split(".")))
        except LauncherError as error:
            if not rebuild:
                raise LauncherError(
                    f".venv uses Python {existing}, which is unsupported. "
                    "Run `python main.py --repair` to rebuild it.",
                    EXIT_PREFLIGHT,
                ) from error
            existing = None
    elif venv.exists() and not rebuild:
        raise LauncherError(
            ".venv exists but is broken. Run `python main.py --repair` to rebuild it.",
            EXIT_PREFLIGHT,
        )
    if existing is None:
        if venv.exists():  # only reached with explicit --repair
            print(f"Rebuilding {venv} (controlled repair) …", file=sys.stderr)
            shutil.rmtree(venv)
        create_venv(venv)
        existing = venv_version(venv)
        if existing is None:
            raise LauncherError("virtual environment creation failed", EXIT_DEPS)
    report.add("ok", "venv", f"{venv} (Python {existing})")

    python = venv_python(venv)
    fingerprint = environment_fingerprint(root, lock_sha, existing, version)
    if force or rebuild or read_state(venv).get("fingerprint") != fingerprint:
        print("Installing locked dependencies …", file=sys.stderr)
        _pip(
            python,
            "install",
            "--require-hashes",
            "--no-deps",
            "--only-binary",
            ":all:",
            "-r",
            str(root / LOCKFILE),
        )
        _pip(python, "install", "--no-deps", "--no-build-isolation", "--no-index", "-e", str(root))
        (venv / STATE_FILE).write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
        report.add("ok", "dependencies", "installed from lockfile")
    else:
        report.add("ok", "dependencies", "up to date (lockfile fingerprint unchanged)")

    cleared = unhide_pth_files(venv)
    if cleared:
        report.add("warn", "pth-files", "cleared macOS hidden flag: " + ", ".join(cleared))
    found = installed_version(python, package)
    if found != version:
        raise LauncherError(
            f"{package} is not importable at version {version} (found {found}); "
            "run `python main.py --force-setup`",
            EXIT_DEPS,
        )
    report.add("ok", "package", f"{package} {found} installed")
    return python


# ------------------------------------------------------------------------- main


def run_app(python: Path, args: list[str], root: Path = ROOT) -> int:
    # Put src/ on the path explicitly: the editable install's .pth file can be ignored
    # (e.g. macOS hides files in iCloud-synced folders and Python >= 3.13 skips hidden
    # .pth files), and the launched app must not depend on it.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(root / "src"), env.get("PYTHONPATH", "")])
    )
    return subprocess.run([str(python), "-m", "photoproof", *args], check=False, env=env).returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PhotoProof launcher", add_help=True)
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--doctor", action="store_true", help="diagnose; change nothing")
    parser.add_argument(
        "--repair", action="store_true", help="controlled repair (may rebuild .venv)"
    )
    parser.add_argument("--dry-run", action="store_true", help="with --repair: only show actions")
    parser.add_argument("--force-setup", action="store_true", help="reinstall locked dependencies")
    parser.add_argument(
        "--ignore-blacklist",
        action="store_true",
        help="administrative bypass of plugins.blacklist (audited)",
    )
    return parser


def split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Launcher flags come before the first positional argument; everything from the
    command onwards is forwarded untouched (so ``repair --dry-run`` keeps its flag)."""
    for index, arg in enumerate(argv):
        if not arg.startswith("-"):
            return argv[:index], argv[index:]
    return argv, []


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    own, forwarded = split_argv(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(own)
    try:
        identity = load_identity(root)
        app = identity["application"]
        if args.version:
            print(f"{app['name']} {app['version']}")
            return EXIT_OK

        report = Report()
        python_text = check_python(identity)
        report.add("ok", "python", f"{python_text} ({sys.executable})")
        system, arch = check_platform(identity)
        report.add("ok", "platform", f"{system} {arch}")
        check_hardware(identity, root, report)

        if args.doctor:
            return doctor(root, identity, report, forwarded)

        if args.repair and args.dry_run:
            existing = venv_version(root / VENV_DIR)
            print("Repair plan (dry run):")
            if existing is None or not _supported(identity, existing):
                print("  - rebuild .venv (controlled)")
            print("  - reinstall locked dependencies")
            if existing:
                return run_app(venv_python(root / VENV_DIR), ["repair", "--dry-run"], root)
            return EXIT_OK

        python = ensure_environment(
            root, identity, force=args.force_setup, rebuild=args.repair, report=report
        )
        if args.repair:
            print(report.render())
            return run_app(python, ["repair"], root)
        if os.environ.get("PHOTOPROOF_LAUNCHER_VERBOSE"):
            print(report.render(), file=sys.stderr)
        prefix = ["--ignore-blacklist"] if args.ignore_blacklist else []
        # The CLI runs preflight itself for commands that start the application;
        # doctor/repair/config/plugins deliberately skip it (and its SAFE repairs).
        return run_app(python, [*prefix, *(forwarded or ["status"])], root)
    except LauncherError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.code
    except KeyboardInterrupt:
        return 130


_TOOLING = {"pip", "setuptools", "wheel"}


def unlocked_packages(root: Path, python: Path) -> list[str]:
    """Distributions in the venv that no lockfile pins (left over from older locks or
    installed by hand). They are reported, never removed automatically."""

    def norm(name: str) -> str:
        return "-".join(name.replace("_", "-").replace(".", "-").lower().split("-"))

    locked = {norm(_PROJECT_NAME)} | _TOOLING
    for lock in root.glob("requirements*.lock"):
        for line in lock.read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith((" ", "#")):
                locked.add(norm(line.split("==", 1)[0]))
    code = (
        "import importlib.metadata as m; "
        "print('\\n'.join(d.metadata['Name'] for d in m.distributions()))"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False)
    installed = {norm(n) for n in result.stdout.split() if n}
    return sorted(installed - locked)


def doctor(root: Path, identity: Identity, report: Report, forwarded: list[str]) -> int:
    """Launcher-level diagnostics, then the application's own ``doctor``. Changes nothing."""
    app = identity["application"]
    venv = root / VENV_DIR
    existing = venv_version(venv)
    report.add(
        "ok" if existing else "fail",
        "venv",
        f"Python {existing}" if existing else "missing or broken (run main.py)",
    )
    try:
        lock_sha = verify_lockfile(root)
        report.add("ok", "lockfile", "pinned and hashed")
    except LauncherError as error:
        lock_sha = ""
        report.add("fail", "lockfile", str(error))
    if existing and lock_sha:
        current = environment_fingerprint(root, lock_sha, existing, app["version"])
        in_sync = read_state(venv).get("fingerprint") == current
        report.add(
            "ok" if in_sync else "warn",
            "dependencies",
            "match lockfile" if in_sync else "drift detected; run main.py to sync",
        )
    if existing:
        extra = unlocked_packages(root, venv_python(venv))
        if extra:
            report.add("warn", "unlocked", "installed but in no lockfile: " + ", ".join(extra))
    print(f"{app['name']} {app['version']} launcher doctor\n{report.render()}", flush=True)
    if not existing:
        return EXIT_PREFLIGHT
    return run_app(venv_python(venv), ["doctor", *forwarded], root)


def _supported(identity: Identity, version: str) -> bool:
    try:
        check_python(identity, tuple(int(p) for p in version.split(".")))
    except LauncherError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
