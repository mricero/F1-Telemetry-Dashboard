"""Write requirements.lock from the versions currently installed (REPO-02).

Without a lock, CI and a new contributor resolve different versions - and
this project has already been bitten by a livef1 patch release changing
parsing shapes. The floors in ``requirements*.txt`` say what the code needs;
the lock says what is known to work.

    python -m pip install -r requirements-dev.txt
    python scripts/lock_requirements.py
"""

import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("requirements.txt", "requirements-dev.txt")
LOCK_FILE = PROJECT_ROOT / "requirements.lock"

HEADER = """\
# Pinned versions this project is known to work with.
#
# Regenerate after changing requirements*.txt:
#   python -m pip install -r requirements-dev.txt
#   python scripts/lock_requirements.py
#
# Install exactly these with:  pip install -r requirements.lock
"""


def direct_requirements() -> list:
    names = []
    for source in SOURCES:
        for line in (PROJECT_ROOT / source).read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            names.append(Requirement(line).name)
    return sorted(set(names), key=str.lower)


def main() -> int:
    pinned, missing = [], []
    for name in direct_requirements():
        try:
            pinned.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)

    if missing:
        print(
            "not installed, so it cannot be locked: " + ", ".join(missing),
            file=sys.stderr,
        )
        print("install the dev requirements first, then re-run", file=sys.stderr)
        return 1

    LOCK_FILE.write_text(HEADER + "\n" + "\n".join(pinned) + "\n", encoding="utf-8")
    print(f"wrote {len(pinned)} pins to {LOCK_FILE.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
