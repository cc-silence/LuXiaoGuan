"""Validate tracked source and CI configuration without connecting to a router."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_SKIPS = {"tests.test_native_window_visibility": 1}


def tracked_paths():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [name.decode("utf-8") for name in raw.split(b"\0") if name]


def check_source():
    paths = tracked_paths()
    violations = []
    secrets = [
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,})\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ]
    text_suffixes = {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".qss", ".js", ".toml", ".ini"}
    for name in paths:
        parts = Path(name).parts
        if (parts[0] in {"build", "dist", "RouterManagerData", "offline_packages"}
                or any(part in {"node_modules", ".wrangler", "python_runtime", "__pycache__"} for part in parts)
                or (parts[0] == "config" and name != "config/app_update.json")
                or Path(name).name.startswith(".env")
                or Path(name).name == ".dev.vars"
                or Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
            violations.append(name + ": local/private file must not be tracked")
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            ast.parse(text, filename=name)
        if any(pattern.search(text) for pattern in secrets):
            violations.append(name + ": possible credential; inspect locally without logging values")
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    if workflow.get("permissions") != {"contents": "read"}:
        violations.append("CI must have read-only repository permissions")
    if "pull_request_target" in workflow.get("on", {}):
        violations.append("CI must not execute PR code in pull_request_target")
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step and not re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", step["uses"]):
                violations.append("CI actions must use verified full commit pins")
    if "* @cc-silence" not in (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8"):
        violations.append("Repository owner must own code reviews")
    for path in (ROOT / ".github").rglob("*.yml"):
        yaml.safe_load(path.read_text(encoding="utf-8"))
    if violations:
        raise ValueError("\n".join(violations))
    print("Tracked source, collaboration templates and read-only CI configuration verified", flush=True)


def check_audit(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = report.get("modules", [])
    expected = {"tests." + p.stem for p in (ROOT / "tests").glob("test_*.py")}
    names = [row["module"] for row in rows]
    if set(names) != expected or len(names) != len(set(names)):
        raise ValueError("Audit must contain every test module exactly once")
    failures = [row["module"] for row in rows if row["status"] != "passed" or row["tests"] < 1
                or row["skipped"] > ALLOWED_SKIPS.get(row["module"], 0)]
    if failures:
        raise ValueError("Failed, empty or unexpectedly skipped audit modules: " + ", ".join(failures))
    print("Complete offline audit verified: " + str(len(rows)) + " modules", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-summary", type=Path)
    args = parser.parse_args()
    check_source()
    if args.audit_summary:
        check_audit(args.audit_summary)


if __name__ == "__main__":
    main()
