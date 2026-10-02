"""Convert a legacy pickle replay (``*.pkl``) into the Parquet replay format (REPO-24).

Replays were pickles before HIST-02. Unpickling runs arbitrary code from the
file, so the app refuses them; this script reads one **you created yourself**
with ``allow_pickle=True`` and writes it back through
``DataSourceManager.save_replay`` as a directory of Parquet tables plus
``meta.json``.

    python scripts/convert_legacy_replay.py "replay_sessions/Abu Dhabi Grand Prix_R_20260708_072230.pkl" --trust
    python scripts/convert_legacy_replay.py old.pkl --trust --move-original-to _to_delete

Without ``--trust`` nothing is read. The original is left in place unless
``--move-original-to`` names a folder for it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Keys a loaded replay carries that describe the load, not the session; the
# loader sets them again on every load.
RUNTIME_KEYS = ("source", "is_live", "live_client")
TIMESTAMP_SUFFIX = re.compile(r"_\d{8}_\d{6}$")


def replay_name(path: Path) -> str:
    """The name to save under: the file stem without its old save timestamp.

    ``save_replay`` appends a fresh ``_YYYYMMDD_HHMMSS`` itself.
    """
    return TIMESTAMP_SUFFIX.sub("", path.stem) or "replay"


def convert(path: Path, replay_dir: Path | None = None, manager=None) -> Path:
    """Load the trusted pickle at ``path`` and save it as a Parquet replay."""
    if manager is None:
        from data.source_manager import DataSourceManager

        manager = DataSourceManager(replay_dir=str(replay_dir) if replay_dir else None)
    data = manager._load_legacy_pickle(path, allow_pickle=True)
    session = {key: value for key, value in data.items() if key not in RUNTIME_KEYS}
    return Path(manager.save_replay(session, replay_name(path)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pickle", type=Path, help="the legacy .pkl replay")
    parser.add_argument(
        "--trust",
        action="store_true",
        help="confirm you created this file yourself; unpickling runs code from it",
    )
    parser.add_argument(
        "--replay-dir",
        type=Path,
        default=None,
        help="where to write the converted replay (default: the app's replay folder)",
    )
    parser.add_argument(
        "--move-original-to",
        type=Path,
        default=None,
        help="move the .pkl into this folder after a successful conversion",
    )
    args = parser.parse_args(argv)

    if not args.pickle.is_file():
        print(f"not a file: {args.pickle}", file=sys.stderr)
        return 1
    if not args.trust:
        print(
            "Refusing to unpickle without --trust: loading a pickle runs code from the "
            "file. Pass --trust only for a replay you saved yourself.",
            file=sys.stderr,
        )
        return 2

    target = convert(args.pickle, args.replay_dir)
    print(f"wrote {target}")
    if args.move_original_to is not None:
        args.move_original_to.mkdir(parents=True, exist_ok=True)
        moved = shutil.move(str(args.pickle), str(args.move_original_to / args.pickle.name))
        print(f"moved the original to {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
