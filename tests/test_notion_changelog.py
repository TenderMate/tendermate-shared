from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import requests

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "notion_changelog.py"
_spec = importlib.util.spec_from_file_location("notion_changelog", _SCRIPT)
assert _spec is not None and _spec.loader is not None
changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog)


def test_select_version_tag_picks_highest_semver() -> None:
    assert changelog.select_version_tag(["v0.2.0", "v0.10.0", "v0.9.9", "v1"]) == "0.10.0"


def test_select_version_tag_picks_new_release_not_the_previous() -> None:
    current = "v0.2.1"
    new_release = "v0.2.2"
    chosen = changelog.select_version_tag([current, "v0.2.0", "v0.1.4", new_release])
    assert chosen == "0.2.2"
    assert chosen != current.removeprefix("v")


def test_resolve_version_uses_tag_ref_over_older_tags() -> None:
    assert changelog.resolve_version("v0.2.2", ["v0.2.1", "v0.2.0"]) == "0.2.2"


def test_resolve_version_falls_back_to_newest_tag_for_a_branch() -> None:
    chosen = changelog.resolve_version("main", ["v0.2.1", "v0.2.0", "v0.2.2"])
    assert chosen == "0.2.2"
    assert chosen != "0.2.1"


def test_read_version_uses_newest_tag_when_ref_is_a_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHANGELOG_REF_NAME", raising=False)
    monkeypatch.setenv("GITHUB_REF_NAME", "feature/notion-changelog-entry")
    monkeypatch.setattr(changelog, "list_version_tags", lambda: ["v0.2.0", "v0.2.1", "v0.1.4"])
    assert changelog.read_version() == "0.2.1"


def test_select_version_tag_rejects_injection() -> None:
    with pytest.raises(changelog.ChangelogError):
        changelog.select_version_tag(["v1.2.3\nheading", "not-a-tag"])


def test_redact_removes_live_secret_and_known_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_KEY", "sk-liveSecretValue99")
    text = "key=sk-liveSecretValue99 and ghp_abcdefghijklmnopqrst and token=abc"
    cleaned = changelog.redact(text)
    assert "sk-liveSecretValue99" not in cleaned
    assert "ghp_abcdefghijklmnopqrst" not in cleaned
    assert "token=abc" not in cleaned
    assert "[REDACTED]" in cleaned


def test_log_does_not_print_the_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "ntn_supersecretvalue")
    changelog.log("failed with ntn_supersecretvalue")
    captured = capsys.readouterr()
    assert "ntn_supersecretvalue" not in captured.err
    assert "ntn_supersecretvalue" not in captured.out
    assert "[REDACTED]" in captured.err


def test_sanitize_bullet_drops_links_and_control_characters() -> None:
    assert changelog.sanitize_bullet("Ship the health check.") == "Ship the health check."
    assert changelog.sanitize_bullet("see https://evil.example/x") == ""
    assert changelog.sanitize_bullet("line\nPull request: http://evil") == ""


def test_parse_changes_ignores_extra_structure() -> None:
    payload = json.dumps(
        {
            "changes": ["Ship the health check.", {"type": "heading_1"}, "Drop unused calls."],
            "children": [{"type": "code"}],
        }
    )
    assert changelog.parse_changes(payload) == [
        "Ship the health check.",
        "Drop unused calls.",
    ]


def test_blocks_are_only_the_fixed_types() -> None:
    url = changelog.pull_request_url("TenderMate", "tendermate-shared", 189)
    blocks = changelog.changelog_blocks("0.2.0", url, ["Ship the health check."])
    assert [block["type"] for block in blocks] == [
        "paragraph",
        "paragraph",
        "paragraph",
        "bulleted_list_item",
    ]
    link = blocks[1]["paragraph"]["rich_text"][1]["text"]["link"]["url"]
    assert link == "https://github.com/TenderMate/tendermate-shared/pull/189"
    assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "version 0.2.0"


def test_choose_pull_request_ignores_attacker_url() -> None:
    payload = [
        {"number": 4, "html_url": "https://evil.example/pull/1"},
        {"number": 12, "html_url": "javascript:alert(1)"},
    ]
    url = changelog.choose_pull_request(payload, "TenderMate", "tendermate-shared")
    assert url == "https://github.com/TenderMate/tendermate-shared/pull/12"


def test_pinned_request_refuses_redirect_and_other_host() -> None:
    class _Response:
        status_code = 302
        is_redirect = True

        def json(self) -> dict[str, str]:
            return {}

    def _fake_request(method: str, url: str, **kwargs: object) -> _Response:
        assert kwargs["allow_redirects"] is False
        return _Response()

    original = requests.request
    requests.request = _fake_request  # type: ignore[assignment]
    try:
        with pytest.raises(changelog.ChangelogError, match="redirect refused"):
            changelog.pinned_request("POST", changelog.DEEPSEEK_ENDPOINT, changelog.DEEPSEEK_ORIGIN)
        with pytest.raises(changelog.ChangelogError, match="host was refused"):
            changelog.pinned_request(
                "POST",
                "https://evil.example/v1/chat/completions",
                "https://evil.example",
            )
    finally:
        requests.request = original  # type: ignore[assignment]


def test_sample_dry_run_prints_entry_without_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CHANGELOG_SAMPLE", "true")
    monkeypatch.setenv("CHANGELOG_DRY_RUN", "true")
    monkeypatch.setenv("DEEPSEEK_KEY", "sk-shouldNotLeak99")
    monkeypatch.setattr(changelog, "read_version", lambda: "0.2.0")

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network call")

    monkeypatch.setattr(changelog.requests, "request", _forbidden)
    changelog.run()
    printed = capsys.readouterr().out
    assert "sk-shouldNotLeak99" not in printed
    assert printed == (
        "version 0.2.0\n"
        "Pull request: https://github.com/TenderMate/tendermate-shared/pull/189\n"
        "Changes:\n"
        "- Add a health response for the service.\n"
        "- Keep release notes free of secrets.\n"
    )


def test_branch_check_writes_only_a_push_to_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CHANGELOG_DRY_RUN", raising=False)
    monkeypatch.delenv("CHANGELOG_LIVE", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    assert changelog.dry_run() is False

    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature/notion-changelog-entry")
    assert changelog.dry_run() is True
    assert "ref is not main" in capsys.readouterr().err

    monkeypatch.setenv(
        "GITHUB_REF",
        "refs/heads/yanger/ten-103-izuchit-vozmozhnost-dlya-podklyucheniya-mcp-s",
    )
    assert changelog.dry_run() is True

    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert changelog.dry_run() is True


def test_changelog_live_skips_the_branch_check_and_logs_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CHANGELOG_DRY_RUN", raising=False)
    monkeypatch.setenv("CHANGELOG_LIVE", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature/notion-changelog-entry")
    assert changelog.dry_run() is False
    assert "CHANGELOG_LIVE is active" in capsys.readouterr().err

    monkeypatch.setenv("CHANGELOG_DRY_RUN", "true")
    assert changelog.dry_run() is True
    assert "CHANGELOG_LIVE is active" not in capsys.readouterr().err


def test_main_logs_fixed_error_without_response_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _fail() -> None:
        raise changelog.ChangelogError("DeepSeek request failed status=401")

    monkeypatch.setattr(changelog, "run", _fail)
    with pytest.raises(SystemExit):
        changelog.main()
    captured = capsys.readouterr()
    assert captured.err.strip() == "DeepSeek request failed status=401"
    assert "sk-" not in captured.err
