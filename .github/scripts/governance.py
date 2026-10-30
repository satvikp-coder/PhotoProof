"""Repository governance checks used by GitHub Actions. Standard library only.

    python .github/scripts/governance.py version [--dist DIR]
    python .github/scripts/governance.py release-notes --tag vX.Y.Z
    python .github/scripts/governance.py pr --base REF --title T --body-file F [--labels L,..]
    python .github/scripts/governance.py summary --junit DIR [--coverage FILE] [--title T]

Every check prints actionable Markdown (also appended to $GITHUB_STEP_SUMMARY).
"""

import argparse
import os
import re
import subprocess
import sys
import tomllib

# Only parses JUnit/coverage XML produced by this workflow's own test jobs.
import xml.etree.ElementTree as ET  # nosec B405
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from photoproof.versioning.version import SemVer, bump_kind  # noqa: E402

TITLE = re.compile(
    r"^(feat|fix|docs|refactor|perf|test|build|ci|chore|style|revert)"
    r"(\([A-Za-z0-9_./-]+\))?(!)?: [^A-Z\s].{2,}[^.]$"
)
ISSUE_REF = re.compile(r"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?|refs?|part of)\s+#\d+")
SECTION = re.compile(r"^## \[(?P<version>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$", re.M)
EXEMPT_LABEL = "governance:exempt"
RELEASE_TITLE = "chore(release):"


def summary(markdown: str) -> None:
    print(markdown)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")


def app_version(text: str | None = None) -> str:
    data = tomllib.loads(text if text is not None else (ROOT / "app.toml").read_text("utf-8"))
    return str(data["application"]["version"])


def changelog_sections(text: str) -> dict[str, tuple[str | None, str]]:
    """Map section name -> (date, body)."""
    matches = list(SECTION.finditer(text))
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match["version"]] = (match["date"], text[match.end() : end].strip())
    return sections


def _has_entries(body: str) -> bool:
    return any(line.lstrip().startswith(("- ", "* ")) for line in body.splitlines())


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


# ---------------------------------------------------------------- commands


def cmd_version(dist: Path | None) -> int:
    version = app_version()
    problems = []
    SemVer.parse(version)
    sections = changelog_sections((ROOT / "CHANGELOG.md").read_text("utf-8"))
    if version not in sections and "Unreleased" not in sections:
        problems.append(f"CHANGELOG.md has neither `## [{version}]` nor `## [Unreleased]`")
    if dist is not None:
        for wheel in sorted(dist.glob("*.whl")):
            with zipfile.ZipFile(wheel) as archive:
                meta = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
                built = re.search(r"^Version: (.+)$", archive.read(meta).decode(), re.M)
            if not built or built[1].strip() != version:
                problems.append(
                    f"{wheel.name} has version {built and built[1]}, app.toml has {version}"
                )
        if not list(dist.glob("*.whl")):
            problems.append(f"no wheel found in {dist}")
    rows = (
        "\n".join(f"| ❌ | {p} |" for p in problems) or "| ✅ | version and changelog consistent |"
    )
    summary(f"### Version consistency — `{version}`\n\n| | Check |\n|---|---|\n{rows}\n")
    return 1 if problems else 0


def cmd_release_notes(tag: str) -> int:
    version = app_version()
    if tag != f"v{version}":
        summary(f"❌ Tag `{tag}` does not match app.toml version `v{version}`.")
        return 1
    sections = changelog_sections((ROOT / "CHANGELOG.md").read_text("utf-8"))
    date, body = sections.get(version, (None, ""))
    if not date or not _has_entries(body):
        summary(
            f"❌ CHANGELOG.md needs a dated `## [{version}] - YYYY-MM-DD` section with entries."
        )
        return 1
    print(body)
    return 0


def evaluate_pr(
    *,
    title: str,
    body: str,
    labels: set[str],
    base_app_toml: str,
    head_app_toml: str,
    changed: set[str],
    base_changelog: str,
    head_changelog: str,
) -> tuple[list[str], list[str]]:
    """Return (failures, warnings). Pure function so it can be unit-tested."""
    failures, warnings = [], []
    if EXEMPT_LABEL in labels:
        warnings.append(f"`{EXEMPT_LABEL}` label present: version/changelog/issue checks skipped")
    if not TITLE.match(title):
        failures.append(
            "PR title must be a Conventional Commit, e.g. `feat(pipeline): add dependency-aware "
            "scheduling` (lowercase description, no trailing period). See CONTRIBUTING.md §4."
        )
    if EXEMPT_LABEL in labels:
        return failures, warnings
    if not ISSUE_REF.search(body):
        failures.append("PR body must reference an issue, e.g. `Closes #42` (CONTRIBUTING.md §5).")

    # Versions are set at release time, so an ordinary PR leaves this alone (CONTRIBUTING.md §6).
    old, new = SemVer.parse(app_version(base_app_toml)), SemVer.parse(app_version(head_app_toml))
    if new != old:
        kind = bump_kind(old, new)
        if kind is None:
            failures.append(
                f"app.toml version must stay `{old}`, or change by one step "
                f"(PATCH `{SemVer(old.major, old.minor, old.patch + 1)}`, "
                f"MINOR `{SemVer(old.major, old.minor + 1, 0)}` or "
                f"MAJOR `{SemVer(old.major + 1, 0, 0)}`) in a release PR;"
                f" found `{new}`. See CONTRIBUTING.md §6."
            )
        else:
            breaking = bool(re.match(r"^\w+(\([^)]*\))?!:", title))
            if breaking and kind != "major":
                failures.append("A breaking change (`!` in the title) requires a MAJOR version.")
            if not title.startswith(RELEASE_TITLE):
                warnings.append(
                    "Only release PRs need to change the version; "
                    "you can leave `app.toml` as it is (CONTRIBUTING.md §6)."
                )

    if "CHANGELOG.md" not in changed:
        failures.append(
            "Add an entry under `## [Unreleased]` in CHANGELOG.md (CONTRIBUTING.md §7)."
        )
    else:
        before = changelog_sections(base_changelog).get("Unreleased", (None, ""))[1]
        after = changelog_sections(head_changelog).get("Unreleased", (None, ""))[1]
        if after == before or not _has_entries(after):
            failures.append("CHANGELOG.md changed but `## [Unreleased]` gained no entries.")

    code = {p for p in changed if p.startswith(("src/", "main.py", "WebKit/"))}
    if code and not any(p.startswith("docs/") for p in changed):
        warnings.append("Code changed but no `docs/` page did; explain in the PR if none applies.")
    if code and not any(p.startswith("tests/") for p in changed):
        warnings.append(
            "Code changed but no tests did; add tests or justify under *Test evidence*."
        )
    return failures, warnings


def cmd_pr(base: str, title: str, body_file: Path, labels: set[str]) -> int:
    changed = set(_git("diff", "--name-only", f"{base}...HEAD").split())
    # Compare with where this branch left main, so main moving on cannot fail an open PR.
    fork_point = _git("merge-base", base, "HEAD").strip()
    failures, warnings = evaluate_pr(
        title=title,
        body=body_file.read_text("utf-8"),
        labels=labels,
        base_app_toml=_git("show", f"{fork_point}:app.toml"),
        head_app_toml=(ROOT / "app.toml").read_text("utf-8"),
        changed=changed,
        base_changelog=_git("show", f"{fork_point}:CHANGELOG.md"),
        head_changelog=(ROOT / "CHANGELOG.md").read_text("utf-8"),
    )
    lines = [f"| ❌ | {f} |" for f in failures] + [f"| ⚠️ | {w} |" for w in warnings]
    lines = lines or ["| ✅ | all governance checks passed |"]
    summary("## PhotoProof · PR governance\n\n| | Result |\n|---|---|\n" + "\n".join(lines) + "\n")
    return 1 if failures else 0


def cmd_summary(junit_dir: Path, coverage: Path | None, title: str) -> int:
    rows, totals = [], [0, 0, 0, 0]
    for report in sorted(junit_dir.glob("**/*.xml")):
        root = ET.parse(report).getroot()  # noqa: S314 - trusted CI artifact
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is None:
            continue
        tests, failures = int(suite.get("tests", 0)), int(suite.get("failures", 0))
        errors, skipped = int(suite.get("errors", 0)), int(suite.get("skipped", 0))
        passed = tests - failures - errors - skipped
        mark = "✅" if failures + errors == 0 else "❌"
        rows.append(
            f"| {mark} | `{report.parent.name} / {report.stem}` | {passed} | "
            f"{failures + errors} | {skipped} | {float(suite.get('time', 0)):.1f}s |"
        )
        for i, value in enumerate((passed, failures + errors, skipped, tests)):
            totals[i] += value
    table = "\n".join(rows) or "| ⚠️ | no test reports found | | | | |"
    text = (
        f"## {title}\n\n| | Environment | Passed | Failed | Skipped | Time |\n"
        f"|---|---|---:|---:|---:|---:|\n{table}\n\n"
        f"**Total:** {totals[0]} passed · {totals[1]} failed · {totals[2]} skipped\n"
    )
    if coverage and coverage.is_file():
        rate = float(ET.parse(coverage).getroot().get("line-rate", 0)) * 100  # noqa: S314
        text += f"\n**Line coverage (Ubuntu, Python 3.12):** {rate:.1f}%\n"
    summary(text)
    return 1 if totals[1] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    version = sub.add_parser("version")
    version.add_argument("--dist", type=Path)
    notes = sub.add_parser("release-notes")
    notes.add_argument("--tag", required=True)
    pr = sub.add_parser("pr")
    pr.add_argument("--base", required=True)
    pr.add_argument("--title", required=True)
    pr.add_argument("--body-file", type=Path, required=True)
    pr.add_argument("--labels", default="")
    report = sub.add_parser("summary")
    report.add_argument("--junit", type=Path, required=True)
    report.add_argument("--coverage", type=Path)
    report.add_argument("--title", default="PhotoProof CI")
    args = parser.parse_args(argv)
    if args.command == "version":
        return cmd_version(args.dist)
    if args.command == "release-notes":
        return cmd_release_notes(args.tag)
    if args.command == "pr":
        return cmd_pr(
            args.base, args.title, args.body_file, set(filter(None, args.labels.split(",")))
        )
    return cmd_summary(args.junit, args.coverage, args.title)


if __name__ == "__main__":
    sys.exit(main())
