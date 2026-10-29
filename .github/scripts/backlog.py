"""Publish the planning backlog (docs/planning/backlog/*.md) as GitHub issues.

Idempotent: every issue body carries a hidden ``<!-- backlog-id: PP-NNN -->`` marker,
so re-running updates existing issues instead of creating duplicates. Labels and
milestones are created only when missing.

    python .github/scripts/backlog.py check              # validate files, no network
    python .github/scripts/backlog.py publish --repo OWNER/REPO
    python .github/scripts/backlog.py index --repo OWNER/REPO   # rewrite backlog.md

Requires the GitHub CLI (``gh``) authenticated with write access for ``publish``.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKLOG = ROOT / "docs" / "planning" / "backlog"
INDEX = ROOT / "docs" / "planning" / "backlog.md"
MARKER = "<!-- backlog-id: {} -->"
REF = re.compile(r"\bPP-\d{3}\b")

MILESTONES = {
    "M1": (
        "Secure Image Ingestion",
        "Store originals immutably, detect formats, decode safely, "
        "and give plugins a normalized working image.",
    ),
    "M2": (
        "Classical Forensic Detection",
        "Metadata, compression, noise, copy-move and visual consistency detectors as plugins.",
    ),
    "M3": (
        "Reference Image Comparison",
        "Target-versus-reference registration, difference mapping and change masks.",
    ),
    "M4": (
        "Localization and Evidence Fusion",
        "Score normalization, documented fusion, inconclusive-result logic and final heatmaps.",
    ),
    "M5": (
        "Web Application and Reporting",
        "WebKit interface, structured and PDF reports, diagnostics.",
    ),
    "M6": (
        "Security, Evaluation and Performance",
        "Isolation hardening, fuzzing, retention, background jobs, datasets and benchmarks.",
    ),
    "M7": ("Research Extensions", "Optional research detectors and comparative studies."),
}

LABELS = {
    **{
        f"type:{t}": ("1d76db", d)
        for t, d in {
            "feature": "New capability",
            "bug": "Defect",
            "enhancement": "Improvement to existing behaviour",
            "security": "Security hardening or vulnerability",
            "documentation": "Documentation",
            "testing": "Tests and evaluation",
            "research": "Exploratory or optional research",
            "maintenance": "Repository, dependency or process upkeep",
        }.items()
    },
    "priority:P0": ("b60205", "Critical blocker or security issue"),
    "priority:P1": ("d93f0b", "Required for the next milestone"),
    "priority:P2": ("fbca04", "Planned"),
    "priority:P3": ("c2e0c6", "Optional or research"),
    **{
        f"area:{a}": ("5319e7", f"Area: {a}")
        for a in (
            "core",
            "pipeline",
            "plugins",
            "imaging",
            "security",
            "WebKit",
            "storage",
            "database",
            "CI",
            "documentation",
            "testing",
            "performance",
        )
    },
    "effort:small": ("bfdadc", "Up to ~2 days"),
    "effort:medium": ("bfdadc", "~3-7 days"),
    "effort:large": ("bfdadc", "More than a week; consider splitting"),
    "status:ready": ("0e8a16", "Fully specified and unblocked"),
    "status:blocked": ("000000", "Waiting on a dependency"),
    "status:needs-design": ("f9d0c4", "Approach must be agreed first"),
    "status:in-progress": ("0052cc", "Someone is working on it"),
    "governance:exempt": ("eeeeee", "Maintainer-approved exemption from PR governance checks"),
}

DEFINITION_OF_DONE = """
### Definition of Done

Follow the repository [Definition of Done](https://github.com/satvikp-coder/PhotoProof/blob/main/CONTRIBUTING.md#9-definition-of-done):
acceptance criteria met, tests and static analysis passing, documentation updated,
`app.toml` version bumped, `CHANGELOG.md` updated, security considered, reviewed, and
merged through a pull request that references this issue.
"""


@dataclass
class Item:
    id: str
    title: str
    milestone: str
    labels: list[str]
    depends_on: list[str]
    body: str
    path: Path


def load() -> list[Item]:
    items = []
    for path in sorted(BACKLOG.glob("PP-*.md")):
        text = path.read_text(encoding="utf-8")
        _, front, body = text.split("---\n", 2)
        meta = yaml.safe_load(front)
        items.append(
            Item(
                meta["id"],
                meta["title"],
                meta["milestone"],
                meta["labels"],
                meta.get("depends_on", []),
                body.strip(),
                path,
            )
        )
    return items


def check(items: list[Item]) -> list[str]:
    problems = []
    ids = {i.id for i in items}
    titles: dict[str, str] = {}
    required = [
        "## Summary",
        "## Problem Statement",
        "## Objective",
        "## Technical Background",
        "## Functional Requirements",
        "## Technical Implementation",
        "## Integration Requirements",
        "## Security and Edge Cases",
        "## Testing Requirements",
        "## Acceptance Criteria",
        "## Expected Outcome",
        "## Documentation Requirements",
        "## Dependencies",
        "## Priority and Effort",
    ]
    for item in items:
        where = item.path.name
        if not item.path.stem.startswith(item.id):
            problems.append(f"{where}: file name must start with {item.id}")
        if item.title in titles:
            problems.append(f"{where}: duplicate title with {titles[item.title]}")
        titles[item.title] = item.id
        if item.milestone not in MILESTONES:
            problems.append(f"{where}: unknown milestone {item.milestone}")
        for label in item.labels:
            if label not in LABELS:
                problems.append(f"{where}: unknown label {label}")
        for prefix in ("type:", "priority:", "area:", "effort:", "status:"):
            count = sum(label.startswith(prefix) for label in item.labels)
            if count == 0 or (prefix != "area:" and count > 1):
                problems.append(
                    f"{where}: needs {'one' if prefix != 'area:' else 'an'} {prefix} label"
                )
        for section in required:
            if section not in item.body:
                problems.append(f"{where}: missing section {section!r}")
        if "- [ ]" not in item.body:
            problems.append(f"{where}: acceptance criteria need checkboxes")
        for ref in set(REF.findall(item.body)) | set(item.depends_on):
            if ref not in ids:
                problems.append(f"{where}: references unknown {ref}")
        if item.id in item.depends_on:
            problems.append(f"{where}: depends on itself")
        if item.depends_on and "status:ready" in item.labels:
            problems.append(f"{where}: has open dependencies, so it cannot be status:ready")
    return problems


def gh(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, input=input_text, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def milestone_title(key: str) -> str:
    return f"{key} · {MILESTONES[key][0]}"


def render(item: Item, numbers: dict[str, int]) -> str:
    def link(match: re.Match[str]) -> str:
        number = numbers.get(match[0])
        return f"#{number}" if number else match[0]

    body = REF.sub(link, item.body)
    return f"{MARKER.format(item.id)}\n{body}\n{DEFINITION_OF_DONE}"


def existing_issues(repo: str) -> dict[str, dict[str, object]]:
    raw = gh(
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,title,body,url",
    )
    found = {}
    for issue in json.loads(raw):
        match = re.search(r"<!-- backlog-id: (PP-\d{3}) -->", issue["body"] or "")
        if match:
            found[match[1]] = issue
    return found


def publish(items: list[Item], repo: str) -> None:
    labels = {
        entry["name"]
        for entry in json.loads(
            gh("label", "list", "--repo", repo, "--limit", "500", "--json", "name")
        )
    }
    for name, (color, description) in LABELS.items():
        if name not in labels:
            gh(
                "label",
                "create",
                name,
                "--repo",
                repo,
                "--color",
                color,
                "--description",
                description,
            )
    milestones = {
        m["title"] for m in json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100"))
    }
    for key, (_, description) in MILESTONES.items():
        if milestone_title(key) not in milestones:
            gh(
                "api",
                f"repos/{repo}/milestones",
                "-f",
                f"title={milestone_title(key)}",
                "-f",
                f"description={description}",
            )

    found = existing_issues(repo)
    numbers = {pid: int(issue["number"]) for pid, issue in found.items()}  # type: ignore[call-overload]
    # Pass 1: create missing issues (references resolved where possible).
    for item in items:
        if item.id in numbers:
            continue
        url = gh(
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            item.title,
            "--milestone",
            milestone_title(item.milestone),
            "--label",
            ",".join(item.labels),
            "--body-file",
            "-",
            input_text=render(item, numbers),
        ).strip()
        numbers[item.id] = int(url.rsplit("/", 1)[1])
        print(f"created {item.id} -> {url}", flush=True)
        time.sleep(1)  # stay below GitHub's secondary rate limit for content creation
    # Pass 2: update every body so all PP-NNN references link to issue numbers.
    found = existing_issues(repo)
    for item in items:
        body = render(item, numbers)
        if (found[item.id]["body"] or "").strip() != body.strip() or found[item.id][
            "title"
        ] != item.title:
            gh(
                "issue",
                "edit",
                str(numbers[item.id]),
                "--repo",
                repo,
                "--title",
                item.title,
                "--body-file",
                "-",
                input_text=body,
            )
            print(f"updated {item.id} -> #{numbers[item.id]}", flush=True)
            time.sleep(1)
    # Pass 3: native GitHub "blocked by" relationships (idempotent).
    ids = {
        pid: json.loads(gh("api", f"repos/{repo}/issues/{n}"))["id"] for pid, n in numbers.items()
    }
    for item in items:
        current = {
            i["number"]
            for i in json.loads(
                gh("api", f"repos/{repo}/issues/{numbers[item.id]}/dependencies/blocked_by")
            )
        }
        for dep in item.depends_on:
            if numbers[dep] not in current:
                gh(
                    "api",
                    "-X",
                    "POST",
                    f"repos/{repo}/issues/{numbers[item.id]}/dependencies/blocked_by",
                    "-F",
                    f"issue_id={ids[dep]}",
                )
                print(f"linked {item.id} blocked by {dep}", flush=True)
    write_index(items, numbers, repo)


def write_index(items: list[Item], numbers: dict[str, int], repo: str | None) -> None:
    lines = [
        "# Development backlog",
        "",
        "Generated by `.github/scripts/backlog.py` from `docs/planning/backlog/*.md`. "
        "Do not edit by hand.",
        "",
        "Issue sources are the single source of truth. Republishing updates the GitHub "
        "issues in place; the hidden `backlog-id` marker prevents duplicates.",
        "",
    ]
    for key, (name, description) in MILESTONES.items():
        group = [i for i in items if i.milestone == key]
        lines += [
            f"## {key} · {name}",
            "",
            description,
            "",
            "| ID | Issue | Priority | Effort | Area | Depends on |",
            "|---|---|---|---|---|---|",
        ]
        for item in group:
            label = {
                p: next((x.split(":", 1)[1] for x in item.labels if x.startswith(p)), "")
                for p in ("priority:", "effort:")
            }
            areas = ", ".join(x.split(":", 1)[1] for x in item.labels if x.startswith("area:"))
            deps = ", ".join(_ref(d, numbers, repo) for d in item.depends_on) or "—"
            title = item.title.replace("|", "\\|")
            if repo and item.id in numbers:
                title = f"[{title}](https://github.com/{repo}/issues/{numbers[item.id]})"
            lines.append(
                f"| {_ref(item.id, numbers, repo)} | {title} | {label['priority:']} | "
                f"{label['effort:']} | {areas} | {deps} |"
            )
        lines.append("")
    INDEX.write_text("\n".join(lines), encoding="utf-8")


def _ref(pid: str, numbers: dict[str, int], repo: str | None) -> str:
    if repo and pid in numbers:
        return f"[{pid} (#{numbers[pid]})](https://github.com/{repo}/issues/{numbers[pid]})"
    return pid


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    for name in ("publish", "index"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True)
    args = parser.parse_args()
    items = load()
    problems = check(items)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.command == "check":
        print(f"{len(items)} backlog items valid")
        write_index(items, {}, None)
    elif args.command == "publish":
        publish(items, args.repo)
    else:
        found = existing_issues(args.repo)
        write_index(items, {k: int(v["number"]) for k, v in found.items()}, args.repo)  # type: ignore[call-overload]
    return 0


if __name__ == "__main__":
    sys.exit(main())
