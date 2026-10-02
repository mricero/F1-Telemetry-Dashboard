"""The replay player's jsdom tests (tests/js), from pytest when Node is set up.

CI runs them in their own job (`npm ci && node --test` in tests/js). Locally
this runs the same suite once `npm ci` has been run in tests/js, and skips
otherwise, so the default suite needs neither Node nor npm.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent / "js"


def test_the_player_js_suite_passes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    if not (JS_DIR / "node_modules" / "jsdom").is_dir():
        pytest.skip("run `npm ci` in tests/js first")
    # The harness builds the payloads with this interpreter.
    env = {**os.environ, "PYTHON": sys.executable}
    result = subprocess.run(  # a fixed argv, no shell
        [node, "--test"],
        cwd=JS_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
