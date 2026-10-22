"""Run isolated offline test modules with bounded concurrency and durable evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/compatibility-audit")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("modules", nargs="*")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    modules = args.modules or ["tests." + p.stem for p in sorted((ROOT / "tests").glob("test_*.py"))]
    def execute(module):
        if not re.fullmatch(r"tests\.test_[a-z0-9_]+", module):
            raise ValueError("Expected a local test module")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="routermanager-audit-") as temporary:
            env = os.environ.copy()
            env.update(QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
                       ROUTERMANAGER_USER_CONFIG_DIR=str(Path(temporary) / "user"))
            try:
                result = subprocess.run([sys.executable, "-m", "unittest", module, "-q"], cwd=ROOT,
                    env=env, capture_output=True, timeout=args.timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
                status = "passed" if result.returncode == 0 else "failed"
                code = result.returncode
            except subprocess.TimeoutExpired as exc:
                output = ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", errors="replace")
                status, code = "timeout", None
        path = args.output / (module + ".log")
        path.write_text(output, encoding="utf-8")
        tests = re.findall(r"Ran (\d+) tests?", output)
        skipped = re.findall(r"skipped=(\d+)", output)
        row = dict(module=module, status=status, exit_code=code, seconds=round(time.monotonic()-started, 2),
                   tests=int(tests[-1]) if tests else 0, skipped=int(skipped[-1]) if skipped else 0, log=str(path))
        print(module + " " + status + " (" + str(row["tests"]) + " tests)", flush=True)
        return row
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(execute, module) for module in modules]):
            rows.append(future.result())
            report = dict(created_at=datetime.now(timezone.utc).isoformat(),
                          note="Offline regression, not physical router certification; counts include imported test cases.",
                          modules=sorted(rows, key=lambda row: row["module"]))
            (args.output / "summary.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    failed = [row["module"] for row in rows if row["status"] != "passed"]
    print(json.dumps(dict(modules=len(rows), tests=sum(row["tests"] for row in rows), failed=failed)), flush=True)
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
