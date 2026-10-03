"""Process-wide live ingest service (IMPROVEMENTS.md LIVE-09).

``DataSourceManager`` used to build its own :class:`SignalRLiveAdapter`, and
the manager lives in ``st.session_state`` - which is per browser tab. Five
viewers meant five upstream connections to F1's feed, five copies of the
buffers, and a much better chance of being rate-limited or IP-blocked; the
community precedents for that (f1-dash, matteocelani's hosted instance) are
in IMPROVEMENTS.md section 14.

There is one adapter per *process*. Browser sessions only read from it, so
starting and stopping the stream is a process-level action.

A module singleton behind a lock rather than ``@st.cache_resource``: the
adapter must exist for scripts, tests and the smoke tool too, none of which
have a Streamlit runtime.
"""

import contextlib
import threading

from f1dash.data.live_adapter import SignalRLiveAdapter

_lock = threading.Lock()
_adapter: SignalRLiveAdapter | None = None


def get_live_adapter() -> SignalRLiveAdapter:
    """The one live adapter for this process, created on first use."""
    global _adapter
    with _lock:
        if _adapter is None:
            _adapter = SignalRLiveAdapter()
        return _adapter


def reset_live_service() -> None:
    """Drop the adapter, stopping it first. Mainly for tests."""
    global _adapter
    with _lock:
        if _adapter is not None:
            # A half-built client must not block the reset; the failure is
            # logged rather than swallowed silently (see REPO-11).
            with contextlib.suppress(Exception):
                _adapter.stop()
        _adapter = None
