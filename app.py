"""Checkout entry point: runs the packaged Streamlit script (REPO-10).

The app lives in ``src/f1dash/app.py``. This shim keeps the checkout's
documented commands working without an install::

    python -m streamlit run app.py
    python app.py              # f1dash.app relaunches through Streamlit

It stays here, next to ``.streamlit/config.toml``, because Streamlit reads
that file from the working directory before the script runs. ``src`` goes on
``sys.path`` so ``f1dash`` imports from this checkout whether or not it is
installed; nothing heavy is imported here, so ``f1dash.app``'s bare-mode
relaunch still runs before Streamlit's caches are applied.
"""

import runpy
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

runpy.run_path(str(SRC / "f1dash" / "app.py"), run_name="__main__")
