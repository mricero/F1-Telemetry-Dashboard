"""The daily update check (DIST-05). GitHub is mocked; nothing here reaches the network."""

import json

import pytest
import requests

from data import update_check

NOW = 1_800_000_000.0


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def answering(tag=None, status=200, calls=None):
    def get(url, timeout, headers):
        if calls is not None:
            calls.append({"url": url, "timeout": timeout, "headers": headers})
        return FakeResponse(status, {"tag_name": tag} if tag else {})

    return get


def offline(*args, **kwargs):
    raise requests.ConnectionError("no route to host")


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "update_check.json"


@pytest.fixture
def enabled():
    return {"F1_UPDATE_CHECK": "1"}


class TestNotice:
    def test_a_newer_release_is_announced(self, cache, enabled):
        notice = update_check.update_notice(
            "0.9.0", cache_path=cache, now=NOW, get=answering("v0.10.0"), environ=enabled
        )

        assert notice == "Update available: v0.10.0 – run f1dash update"

    def test_the_notice_follows_the_copy_rules(self, cache, enabled):
        """Guideline 5.9: plain text, no exclamation mark, no emoji."""
        notice = update_check.update_notice(
            "0.9.0", cache_path=cache, now=NOW, get=answering("v1.0.0"), environ=enabled
        )

        assert "!" not in notice
        assert {char for char in notice if not char.isascii()} <= {"–"}

    def test_the_same_version_says_nothing(self, cache, enabled):
        assert (
            update_check.update_notice(
                "0.9.0", cache_path=cache, now=NOW, get=answering("v0.9.0"), environ=enabled
            )
            is None
        )

    def test_an_older_release_says_nothing(self, cache, enabled):
        assert (
            update_check.update_notice(
                "0.10.0", cache_path=cache, now=NOW, get=answering("v0.9.0"), environ=enabled
            )
            is None
        )

    def test_offline_says_nothing(self, cache, enabled):
        assert (
            update_check.update_notice(
                "0.9.0", cache_path=cache, now=NOW, get=offline, environ=enabled
            )
            is None
        )

    @pytest.mark.parametrize("status", [403, 404, 500])
    def test_an_error_answer_says_nothing(self, cache, enabled, status):
        assert (
            update_check.update_notice(
                "0.9.0",
                cache_path=cache,
                now=NOW,
                get=answering("v9.0.0", status=status),
                environ=enabled,
            )
            is None
        )

    @pytest.mark.parametrize("value", ["0", "false", "off"])
    def test_it_can_be_disabled(self, cache, value):
        calls = []

        notice = update_check.update_notice(
            "0.9.0",
            cache_path=cache,
            now=NOW,
            get=answering("v1.0.0", calls=calls),
            environ={"F1_UPDATE_CHECK": value},
        )

        assert notice is None
        assert calls == []

    def test_a_prerelease_or_odd_tag_is_ignored(self, cache, enabled):
        assert (
            update_check.update_notice(
                "0.9.0", cache_path=cache, now=NOW, get=answering("nightly"), environ=enabled
            )
            is None
        )


class TestOncePerDay:
    def test_a_second_check_within_a_day_uses_the_cache(self, cache, enabled):
        calls = []
        get = answering("v0.10.0", calls=calls)

        update_check.update_notice("0.9.0", cache_path=cache, now=NOW, get=get, environ=enabled)
        notice = update_check.update_notice(
            "0.9.0", cache_path=cache, now=NOW + 3600, get=get, environ=enabled
        )

        assert len(calls) == 1
        assert notice is not None

    def test_a_day_later_it_asks_again(self, cache, enabled):
        calls = []
        get = answering("v0.10.0", calls=calls)

        update_check.update_notice("0.9.0", cache_path=cache, now=NOW, get=get, environ=enabled)
        update_check.update_notice(
            "0.9.0", cache_path=cache, now=NOW + 86_401, get=get, environ=enabled
        )

        assert len(calls) == 2

    def test_a_failed_check_is_not_retried_on_every_rerun(self, cache, enabled):
        calls = []

        def failing(url, timeout, headers):
            calls.append(url)
            raise requests.Timeout("slow")

        for offset in (0, 10, 20):
            update_check.update_notice(
                "0.9.0", cache_path=cache, now=NOW + offset, get=failing, environ=enabled
            )

        assert len(calls) == 1

    def test_the_request_has_a_3_second_timeout(self, cache, enabled):
        calls = []

        update_check.update_notice(
            "0.9.0",
            cache_path=cache,
            now=NOW,
            get=answering("v1.0.0", calls=calls),
            environ=enabled,
        )

        assert calls[0]["timeout"] == 3.0
        assert calls[0]["url"].endswith("/repos/mricero/F1-Telemetry-Dashboard/releases/latest")

    def test_a_corrupt_cache_is_replaced(self, cache, enabled):
        cache.write_text("{not json", encoding="utf-8")

        notice = update_check.update_notice(
            "0.9.0", cache_path=cache, now=NOW, get=answering("v0.10.0"), environ=enabled
        )

        assert notice is not None
        assert json.loads(cache.read_text("utf-8"))["latest"] == "v0.10.0"

    def test_an_unwritable_cache_is_not_an_error(self, tmp_path, enabled):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")

        notice = update_check.update_notice(
            "0.9.0",
            cache_path=blocker / "update_check.json",
            now=NOW,
            get=answering("v0.10.0"),
            environ=enabled,
        )

        assert notice is not None

    def test_the_default_cache_is_in_the_user_cache_directory(self):
        assert update_check.default_cache_path().name == "update_check.json"
        assert "f1dash" in str(update_check.default_cache_path())


class TestVersions:
    @pytest.mark.parametrize(
        ("latest", "current", "newer"),
        [
            ("v0.10.0", "0.9.0", True),
            ("v0.9.1", "0.9.0", True),
            ("v1.0", "0.9.0", True),
            ("v0.9.0", "0.9.0", False),
            ("v0.9", "0.9.0", False),
            ("v0.8.9", "0.9.0", False),
            ("v1.0.0rc1", "0.9.0", False),
            (None, "0.9.0", False),
            ("v1.0.0", "0+unknown", False),
        ],
    )
    def test_is_newer(self, latest, current, newer):
        assert update_check.is_newer(latest, current) is newer


class TestUpdateCommand:
    def test_a_git_install_reinstalls_the_tag(self):
        assert update_check.update_command("v0.10.0") == [
            "uv",
            "tool",
            "install",
            "--reinstall",
            "git+https://github.com/mricero/F1-Telemetry-Dashboard@v0.10.0",
        ]

    def test_without_a_tag_it_uses_main(self):
        assert update_check.update_command(None)[-1].endswith("@main")

    def test_a_registry_install_upgrades(self):
        assert update_check.update_command("v1.0.0", from_registry=True) == [
            "uv",
            "tool",
            "upgrade",
            "f1dash",
        ]

    def test_a_checkout_is_not_a_registry_install(self, monkeypatch):
        def missing(name):
            raise update_check.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(update_check.metadata, "distribution", missing)

        assert update_check.installed_from_registry() is False
