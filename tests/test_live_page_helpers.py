"""The Live page's small helpers: freshness (LIVE-23), the token helper
(LIVE-24) and the recorder error (LIVE-31)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 10, 10, 13, 30, tzinfo=UTC)


def _jwt(exp: datetime) -> str:
    def part(value: dict) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'exp': int(exp.timestamp())})}.sig"


class TestFreshness:
    def test_a_recent_update_counts_up(self):
        from f1dash.ui.layout import freshness_caption

        beat = (NOW - timedelta(seconds=3)).isoformat()
        assert freshness_caption(beat, now=NOW) == "Last update 3 s ago"

    def test_past_thirty_seconds_it_says_no_update(self):
        from f1dash.ui.layout import freshness_caption

        beat = (NOW - timedelta(seconds=45)).isoformat()
        assert freshness_caption(beat, now=NOW) == "No update for 45 s"

    def test_before_anything_arrived(self):
        from f1dash.ui.layout import freshness_caption

        assert freshness_caption(None, now=NOW) == "No update received yet"


class TestTokenHelper:
    def test_a_past_exp_reads_expired(self):
        from f1dash.ui.layout import token_line

        line = token_line(_jwt(NOW - timedelta(days=1)), now=NOW)
        assert "expired" in line

    def test_a_valid_token_names_its_days(self):
        from f1dash.ui.layout import token_line

        line = token_line(_jwt(NOW + timedelta(days=3, hours=2)), now=NOW)
        assert line == "Subscription token valid for 3 more day(s)"

    def test_no_token_says_what_still_works(self):
        from f1dash.ui.layout import token_line

        assert token_line(None).startswith("No subscription token")

    def test_saving_writes_exactly_one_line_and_keeps_the_rest(self, tmp_path, monkeypatch):
        from f1dash.data.live_adapter import TOKEN_ENV_VAR
        from f1dash.data.token_store import save_subscription_token

        # setenv first so the undo restores "unset": save_subscription_token
        # writes os.environ, and delenv alone records nothing to undo.
        monkeypatch.setenv(TOKEN_ENV_VAR, "")
        monkeypatch.delenv(TOKEN_ENV_VAR)
        env = tmp_path / ".env"
        env.write_text(
            f"LOG_LEVEL=INFO\n{TOKEN_ENV_VAR}=old\nREPLAY_DIR=x\n{TOKEN_ENV_VAR}=older\n",
            encoding="utf-8",
        )

        save_subscription_token("  new-token  ", env)

        lines = env.read_text(encoding="utf-8").splitlines()
        assert lines == ["LOG_LEVEL=INFO", f"{TOKEN_ENV_VAR}=new-token", "REPLAY_DIR=x"]

    def test_a_multi_line_paste_is_refused(self, tmp_path, monkeypatch):
        from f1dash.data.live_adapter import TOKEN_ENV_VAR
        from f1dash.data.token_store import save_subscription_token

        # setenv first so the undo restores "unset": save_subscription_token
        # writes os.environ, and delenv alone records nothing to undo.
        monkeypatch.setenv(TOKEN_ENV_VAR, "")
        monkeypatch.delenv(TOKEN_ENV_VAR)
        with pytest.raises(ValueError):
            save_subscription_token("a\nb", tmp_path / ".env")
        assert not (tmp_path / ".env").exists()


class TestRecorderErrorIsShown:
    def test_the_live_page_says_recording_stopped(self):
        from streamlit.testing.v1 import AppTest

        def script():
            from f1dash.data.live_adapter import SignalRLiveAdapter
            from f1dash.ui.layout import render_feed_status

            adapter = SignalRLiveAdapter()
            adapter.recorder_error = "Recording stopped: OSError: No space left on device"
            render_feed_status(adapter)

        app_test = AppTest.from_function(script, default_timeout=30)
        app_test.run()

        assert not app_test.exception
        assert "No space left on device" in app_test.warning[0].value
