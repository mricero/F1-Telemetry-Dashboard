"""F1 Telemetry Dashboard: replay, timing and telemetry on Streamlit (REPO-10).

``f1dash.app`` is the Streamlit script, ``f1dash.cli`` the ``f1dash``
command, and ``data`` / ``processing`` / ``ui`` the three layers. Importing
the package itself does nothing: :mod:`f1dash.config` loads ``.env``, so it
runs only when something asks for it - ``f1dash.__version__`` included.
"""

from __future__ import annotations


def __getattr__(name: str) -> str:
    if name == "__version__":
        from f1dash.config import __version__

        return __version__
    raise AttributeError(f"module 'f1dash' has no attribute {name!r}")
