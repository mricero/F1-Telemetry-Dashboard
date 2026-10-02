"""Re-lock requirements*.lock from requirements*.txt with uv (REPO-20).

Both locks are universal (one file for every OS and Python from the 3.11
floor up; uv writes the environment markers itself) and carry hashes, so CI
installs them with ``--require-hashes``. CI re-runs this script and fails on
any diff, which is what makes a stale lock turn the build red.

    python scripts/lock_requirements.py            # keep current pins where possible
    python scripts/lock_requirements.py --upgrade  # move everything to the newest allowed
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCKS = {"requirements.txt": "requirements.lock", "requirements-dev.txt": "requirements-dev.lock"}
PYTHON_FLOOR = "3.11"


def compile_command(source: str, output: str, upgrade: bool = False) -> list[str]:
    command = [
        "uv",
        "pip",
        "compile",
        source,
        "--universal",
        "--python-version",
        PYTHON_FLOOR,
        "--generate-hashes",
        "--quiet",
        "--output-file",
        output,
    ]
    if upgrade:
        command.append("--upgrade")
    return command


def main(argv: list[str]) -> int:
    if shutil.which("uv") is None:
        print("uv is not installed: https://docs.astral.sh/uv/", file=sys.stderr)
        return 1
    upgrade = "--upgrade" in argv
    for source, output in LOCKS.items():
        subprocess.run(compile_command(source, output, upgrade), cwd=PROJECT_ROOT, check=True)
        print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
