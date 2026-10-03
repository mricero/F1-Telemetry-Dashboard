"""The ``f1dash`` command (DIST-02).

Streamlit apps start with ``streamlit run app.py`` from the folder that holds
``.streamlit/config.toml``. An installed tool has neither a known path nor
that working directory, so this finds the package's own ``f1dash/app.py``,
turns the bundled ``config.toml`` into ``--section.key=value`` flags (theme,
radius, toolbar, ``gatherUsageStats = false``) and hands both to Streamlit's
own CLI. Going through ``streamlit run`` keeps ``app.py``'s bare-mode
relaunch out of play.

The config file is the checkout's ``.streamlit/config.toml``; the wheel
carries a copy at ``f1dash/.streamlit/config.toml`` (REPO-10), which is
looked for first.

    f1dash                       # open the dashboard in the browser
    python -m f1dash             # the same, from a checkout
    f1dash --port 8600 --no-browser
    f1dash paths                 # where cache, replays, records and .env live
    f1dash update                # reinstall the newest release with uv
    f1dash --version

Importing :mod:`f1dash.config` loads ``.env`` from the user config directory (or the
checkout) before Streamlit starts.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

from f1dash import config

HERE = Path(__file__).resolve().parent
APP_PATH = HERE / "app.py"


def _streamlit_config() -> Path:
    """The wheel's bundled copy, else the checkout's ``.streamlit/config.toml``."""
    bundled = HERE / ".streamlit" / "config.toml"
    if bundled.is_file():
        return bundled
    return config.PROJECT_ROOT / ".streamlit" / "config.toml"


STREAMLIT_CONFIG = _streamlit_config()
DEFAULT_PORT = 8501
PORT_ATTEMPTS = 50


def _flag_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def streamlit_flags(config_path: Path = STREAMLIT_CONFIG) -> list[str]:
    """Every ``[section] key = value`` of ``config.toml`` as ``--section.key=value``."""
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    flags = []
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            flags.append(f"--{section}.{key}={_flag_value(value)}")
    return flags


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return False
    return True


def choose_port(requested: int | None, is_free=port_is_free) -> int:
    """The requested port as given; by default 8501 or the next free one."""
    if requested is not None:
        return requested
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_ATTEMPTS):
        if is_free(port):
            return port
    return DEFAULT_PORT


def streamlit_argv(port: int, no_browser: bool = False) -> list[str]:
    argv = ["run", str(APP_PATH), *streamlit_flags()]
    if no_browser:
        argv.append("--server.headless=true")
    argv += ["--server.port", str(port)]
    return argv


def run_dashboard(port: int | None = None, no_browser: bool = False) -> int:
    from streamlit.web import cli as streamlit_cli

    argv = streamlit_argv(choose_port(port), no_browser)
    # Streamlit's click CLI exits by itself; standalone_mode=False returns instead.
    result = streamlit_cli.main(argv, prog_name="streamlit", standalone_mode=False)
    return result if isinstance(result, int) else 0


def print_paths(out=None) -> int:
    out = out or sys.stdout
    rows = [
        ("FastF1 cache", config.config.fastf1_cache_dir),
        ("Replays", config.config.replay_dir),
        ("Records", config.config.metrics_store_path),
        (".env", config.config.env_path),
    ]
    width = max(len(label) for label, _ in rows)
    for label, path in rows:
        print(f"{label.ljust(width)}  {path}", file=out)
    return 0


def run_update(run=subprocess.run, which=shutil.which) -> int:
    from f1dash.data import update_check

    if which("uv") is None:
        print(
            "uv is not on PATH. Install it from https://docs.astral.sh/uv/ and run "
            "f1dash update again.",
            file=sys.stderr,
        )
        return 1
    from_registry = update_check.installed_from_registry()
    tag = None if from_registry else update_check.latest_release_tag()
    if not from_registry and tag is None:
        print("Could not read the latest release from GitHub; installing from main.")
    argv = update_check.update_command(tag, from_registry=from_registry)
    print("Running: " + " ".join(argv))
    return run(argv, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="f1dash", description="Formula 1 replay, timing and telemetry dashboard."
    )
    parser.add_argument("--version", action="version", version=f"f1dash {config.__version__}")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"port to serve on (default {DEFAULT_PORT}, or the next free one)",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    commands = parser.add_subparsers(dest="command", metavar="{paths,update}")
    commands.add_parser("paths", help="print where cache, replays, records and .env live")
    commands.add_parser("update", help="reinstall the newest release with uv")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "paths":
        return print_paths()
    if args.command == "update":
        return run_update()
    return run_dashboard(args.port, args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
