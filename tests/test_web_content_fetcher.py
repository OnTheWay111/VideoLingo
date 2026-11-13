import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/web-content-fetcher/scripts"
CAR_SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/xcar-xhs/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(CAR_SCRIPTS_DIR))

import editorial
import finder
import web_content
import web_session


def session_args(browser="off", profile=None, cookies_file=None, auth_file=None):
    return SimpleNamespace(
        web_session=browser,
        browser_profile=profile,
        cookies_file=cookies_file,
        auth_file=auth_file,
    )


def test_ytdlp_session_is_off_by_default():
    assert web_session.ytdlp_session_args(session_args()) == ["--no-cookies-from-browser"]


def test_ytdlp_session_passes_browser_and_profile():
    args = session_args("chrome", "Profile 1")

    assert web_session.ytdlp_session_args(args) == [
        "--cookies-from-browser", "chrome:Profile 1"
    ]
    assert web_session.session_summary(args) == {
        "mode": "browser", "browser": "chrome", "profile": "Profile 1",
        "cookies_file": None,
    }


def test_ytdlp_session_accepts_cookie_file(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")

    assert web_session.ytdlp_session_args(session_args(cookies_file=str(cookies))) == [
        "--cookies", str(cookies)
    ]


def test_browser_profile_requires_browser():
    with pytest.raises(web_session.SessionError, match="web-session"):
        web_session.ytdlp_session_args(session_args(profile="Default"))


def test_prepare_x_auth_uses_native_browser_profile(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text("XFETCH_AUTH_TOKEN=dummy-auth-token\nXFETCH_CT0=dummy-ct0-token\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web_session.subprocess, "run", fake_run)
    monkeypatch.setattr(web_session.sys, "platform", "darwin")
    env = {}
    args = session_args("chrome", "Profile 2")

    result = web_session.prepare_x_auth(args, env, "python", "xfetch.py")

    assert result["XFETCH_AUTH_TOKEN"] == "dummy-auth-token"
    assert result["XFETCH_CT0"] == "dummy-ct0-token"
    assert "--profile" in commands[0]
    assert "Profile 2" in commands[0]
    assert "dummy-auth-token" not in commands[0]


def test_prepare_x_auth_falls_back_to_cdp(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "auth_from_chrome.py" in cmd[1]:
            return SimpleNamespace(returncode=1, stdout="", stderr="keychain denied")
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text("XFETCH_AUTH_TOKEN=cdp-auth-token\nXFETCH_CT0=cdp-ct0-token\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web_session.subprocess, "run", fake_run)
    monkeypatch.setattr(web_session.sys, "platform", "darwin")

    result = web_session.prepare_x_auth(
        session_args("chrome", "Default"), {}, "python", "xfetch.py"
    )

    assert len(commands) == 2
    assert commands[0][1].endswith("auth_from_chrome.py")
    assert commands[1][1].endswith("auth_from_chrome_cdp.py")
    assert result["XFETCH_AUTH_TOKEN"] == "cdp-auth-token"
    assert result["XFETCH_CT0"] == "cdp-ct0-token"


def test_prepare_x_auth_explicit_auth_file_wins(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("不应再次读取浏览器")

    monkeypatch.setattr(web_session.subprocess, "run", unexpected_run)
    env = {"XFETCH_AUTH_TOKEN": "existing-auth", "XFETCH_CT0": "existing-ct0"}

    result = web_session.prepare_x_auth(
        session_args("chrome", auth_file="explicit.env"), env, "python", "xfetch.py"
    )

    assert result is env
    assert result["XFETCH_AUTH_TOKEN"] == "existing-auth"


def test_prepare_x_auth_imports_cookie_file_without_saving_default_session(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookie export")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web_session.subprocess, "run", fake_run)

    result = web_session.prepare_x_auth(
        session_args(cookies_file=str(cookies)), {}, "python", "xfetch.py"
    )

    assert commands[0][2:4] == ["auth", "import"]
    assert str(cookies) in commands[0]
    assert result["XFETCH_COOKIES"] != str(Path.home() / ".config/xfetch/cookies.json")
    assert Path(result["XFETCH_COOKIES"]).name == "cookies.json"


def test_session_values_are_redacted():
    env = {"XFETCH_AUTH_TOKEN": "secret-auth-value", "XFETCH_CT0": "secret-ct0-value"}

    detail = web_session.redact_session_values(
        "failed: secret-auth-value and secret-ct0-value", env
    )

    assert "secret-auth-value" not in detail
    assert "secret-ct0-value" not in detail
    assert detail.count("<redacted>") == 2


def test_normalize_youtube_entry_matches_finder_fields():
    entry = {
        "id": "abc123",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "extractor_key": "Youtube",
        "title": "Cybertruck off-road review",
        "description": "A car review",
        "uploader": "Car Channel",
        "uploader_id": "@cars",
        "upload_date": "20261109",
        "like_count": 120,
        "comment_count": 12,
        "view_count": 5000,
        "duration": 95,
    }

    item = web_content.normalize_entry(entry)

    assert item["id"] == "youtube-abc123"
    assert item["platform"] == "youtube"
    assert item["author"] == "@cars"
    assert item["date"] == "2026-11-09"
    assert item["likes"] == 120
    assert item["replies"] == 12
    assert item["views"] == 5000
    assert item["duration"] == 95


def test_platform_detection_does_not_trust_lookalike_domains():
    assert web_content.detect_platform("https://x.com/user/status/1") == "x"
    assert web_content.detect_platform("https://x.com.example.org/video") == "web"
    assert web_content.detect_platform("https://youtu.be/abc") == "youtube"


def test_metadata_command_uses_explicit_session_and_playlist_rule():
    args = session_args("firefox", "default-release")

    command = web_content.metadata_command("https://www.tiktok.com/@car/video/1", args, True)

    assert command[:2] == ["yt-dlp", "--ignore-config"]
    assert command[2:4] == ["--cookies-from-browser", "firefox:default-release"]
    assert "--no-playlist" in command
    assert command[-2:] == ["--", "https://www.tiktok.com/@car/video/1"]


def test_download_command_falls_back_when_height_has_no_match(tmp_path):
    args = session_args()
    args.max_height = 480
    item = {"url": "https://x.com/car/status/1"}

    command = web_content.download_command(item, args, tmp_path)

    assert command[command.index("-f") + 1] == "bv*[height<=480]+ba/b[height<=480]/b"


def test_finder_download_reuses_web_session(tmp_path):
    candidate = {"url": "https://x.com/car/status/1"}
    args = session_args("chrome", "Default")
    args.max_height = 1080

    command = finder.build_download_command(candidate, tmp_path, args)

    assert command[:2] == ["yt-dlp", "--ignore-config"]
    assert command[2:4] == ["--cookies-from-browser", "chrome:Default"]
    assert command[-2:] == ["--", candidate["url"]]


def test_cli_writes_normalized_jsonl_and_manifest(monkeypatch, tmp_path):
    entry = {
        "id": "1", "webpage_url": "https://www.instagram.com/p/1/",
        "extractor_key": "Instagram", "title": "Car reel",
    }

    def fake_fetch(target, args, direct_url):
        return [web_content.normalize_entry(entry, target)], None

    monkeypatch.setattr(web_content, "fetch_metadata", fake_fetch)

    result = web_content.main([
        "--url", "https://www.instagram.com/p/1/", "--out", str(tmp_path)
    ])

    assert result == 0
    item = json.loads((tmp_path / "items.jsonl").read_text().strip())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert item["platform"] == "instagram"
    assert manifest["item_count"] == 1
    assert manifest["session"]["mode"] == "off"
    assert manifest["errors"] == []


def test_cli_returns_error_when_all_sources_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        web_content, "fetch_metadata", lambda *args: ([], "login required")
    )

    result = web_content.main([
        "--url", "https://www.tiktok.com/@car/video/1", "--out", str(tmp_path)
    ])

    assert result == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["item_count"] == 0
    assert manifest["errors"][0]["error"] == "login required"


def test_normalize_x_tweet_uses_shared_schema():
    tweet = {
        "id": "123",
        "url": "https://x.com/codex/status/123",
        "created_at": "Mon Nov 09 01:02:03 +0000 2026",
        "text": "OpenAI Codex latest discussion",
        "favorite_count": 20,
        "reply_count": 3,
        "retweet_count": 4,
        "view_count": "500",
        "media": [{"type": "video", "url": "https://pbs.twimg.com/thumb.jpg"}],
        "author": {"screen_name": "codex", "name": "Codex"},
    }

    item = web_content.normalize_x_tweet(tweet)

    assert item["id"] == "x-123"
    assert item["platform"] == "x"
    assert item["author"] == "codex"
    assert item["date"] == "2026-11-09"
    assert item["likes"] == 20
    assert item["replies"] == 3
    assert item["reposts"] == 4
    assert item["views"] == 500
    assert item["thumbnail"] == "https://pbs.twimg.com/thumb.jpg"
    assert web_content.sort_items([item], "views") == [item]


def test_x_search_command_uses_requested_view_and_limit():
    args = SimpleNamespace(type="latest", limit=17)

    command = web_content.x_search_command("codex", args, "python", "xfetch.py")

    assert command == [
        "python", "xfetch.py", "search", "codex", "--type", "latest",
        "-n", "17", "--format", "jsonl",
    ]


def test_collect_x_checks_auth_once_appends_since_dedupes_and_sorts(monkeypatch):
    auth_calls = []
    queries = []
    monkeypatch.setattr(web_content, "resolve_x_python", lambda value: "python")
    monkeypatch.setattr(web_content, "resolve_xfetch", lambda value: "xfetch.py")

    def fake_auth(args, py, xfetch):
        auth_calls.append((py, xfetch))
        return {"SESSION": "dummy"}, None

    def fake_fetch(query, args, env, py, xfetch):
        queries.append(query)
        tweets = [
            {"id": "1", "text": "low", "favorite_count": 2},
            {"id": "2", "text": "high", "favorite_count": 9},
        ]
        return [web_content.normalize_x_tweet(tweet) for tweet in tweets], None

    monkeypatch.setattr(web_content, "x_auth_environment", fake_auth)
    monkeypatch.setattr(web_content, "fetch_x_query", fake_fetch)
    args = SimpleNamespace(
        source="x", query=["codex", "codex"], url=[], python=None, xfetch=None,
        since="2026-11-01", sort="likes", type="latest", limit=10,
    )

    items, errors = web_content.collect(args)

    assert len(auth_calls) == 1
    assert queries == ["codex since:2026-11-01"]
    assert [item["source_id"] for item in items] == ["2", "1"]
    assert errors == []


def test_collect_x_returns_auth_error_without_search(monkeypatch):
    monkeypatch.setattr(web_content, "resolve_x_python", lambda value: "python")
    monkeypatch.setattr(web_content, "resolve_xfetch", lambda value: "xfetch.py")
    monkeypatch.setattr(
        web_content, "x_auth_environment", lambda *args: ({}, "session expired")
    )

    def unexpected_fetch(*args):
        raise AssertionError("会话失败时不应发起搜索")

    monkeypatch.setattr(web_content, "fetch_x_query", unexpected_fetch)
    args = SimpleNamespace(
        source="x", query=["codex"], python=None, xfetch=None,
        since=None, sort="none", type="latest", limit=10,
    )

    items, errors = web_content.collect(args)

    assert items == []
    assert errors == [{"target": "X auth", "error": "session expired"}]


def editorial_item(text, item_id="1"):
    return {
        "id": "x-" + item_id,
        "source_id": item_id,
        "platform": "x",
        "url": "https://x.com/codex/status/" + item_id,
        "text": text,
        "title": text[:120],
    }


def test_topic_aliases_include_codex_umbrella():
    aliases = editorial.topic_aliases(
        topic="OpenAI Codex", queries=['("OpenAI Codex" OR "Codex CLI")']
    )

    assert aliases == ["OpenAI Codex", "Codex CLI", "Codex"]


def test_editorial_rejects_missing_topic_even_with_high_engagement():
    result = editorial.evaluate_item(
        editorial_item("Most people prompt every AI model the same way. Reply below."),
        ["OpenAI Codex", "Codex"],
    )

    assert result["selection"] == "rejected"
    assert result["reject_reasons"] == ["主题缺失"]
    assert result["wechat_score"] == 0


def test_editorial_rejects_topic_mentioned_only_after_first_two_lines():
    result = editorial.evaluate_item(
        editorial_item("Grok Bot 开源神器合集\n兄弟们，这些工具都很强\n有人用它管理 Codex"),
        ["Codex"],
    )

    assert result["selection"] == "rejected"
    assert result["reject_reasons"] == ["非主题主导"]


def test_editorial_keeps_technical_codex_news():
    result = editorial.evaluate_item(
        editorial_item(
            "OpenAI Codex Desktop Remote SSH 多会话认证冲突\n"
            "新客户端替换共享 SSH Socket，导致原会话 GitHub 认证超时。"
            "报告给出了恢复路径和会话隔离建议，属于具体 bug 分析与修复实践。"
        ),
        ["OpenAI Codex", "Codex"],
    )

    assert result["selection"] == "selected"
    assert result["topic_in_lead"] is True
    assert result["wechat_score"] >= 70


def test_editorial_rejects_shallow_and_unsubstantiated_income_posts():
    shallow = editorial.evaluate_item(editorial_item("Codex CLI，大家怎么看？"), ["Codex"])
    hype = editorial.evaluate_item(
        editorial_item("OpenAI Codex 自动交易\n一个月稳赚 5 万美元，一年下来财富自由"),
        ["Codex"],
    )

    assert shallow["selection"] == "rejected"
    assert hype["selection"] == "rejected"
    assert hype["wechat_score"] < 70
    assert any("高收益" in reason for reason in hype["wechat_reasons"])


def test_select_wechat_items_preserves_reject_audit():
    items = [
        editorial_item("Codex CLI now has an agent command center for managing sessions.", "1"),
        editorial_item("Grok 合集\n兄弟们看这个\n第三行才提到 Codex", "2"),
    ]

    selected, rejected, aliases = editorial.select_wechat_items(
        items, queries=['"Codex CLI"'], min_score=70
    )

    assert [item["source_id"] for item in selected] == ["1"]
    assert [item["source_id"] for item in rejected] == ["2"]
    assert aliases == ["Codex CLI", "Codex"]
    assert rejected[0]["reject_reasons"] == ["非主题主导"]


def test_cli_returns_success_when_candidates_exist_but_none_are_suitable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        web_content, "collect",
        lambda args: ([editorial_item("完全无关的 AI 新闻", "1")], []),
    )

    result = web_content.main([
        "--source", "x", "--query", '"OpenAI Codex"',
        "--topic", "OpenAI Codex", "--out", str(tmp_path),
    ])

    assert result == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["candidate_count"] == 1
    assert manifest["item_count"] == 0
    assert manifest["rejected_count"] == 1
    assert (tmp_path / "items.jsonl").read_text() == ""
    assert "主题缺失" in (tmp_path / "rejected.jsonl").read_text()


def test_cli_no_longer_accepts_youtube_source(tmp_path):
    with pytest.raises(SystemExit) as exc:
        web_content.main(["--source", "youtube", "--query", "Codex", "--out", str(tmp_path)])

    assert exc.value.code == 2


def engagement_item(item_id, date, likes=0, replies=0, reposts=0, views=0):
    item = editorial_item(
        "Codex CLI update adds workflow review guidance for agents.", item_id
    )
    item.update({
        "date": date,
        "likes": likes,
        "replies": replies,
        "reposts": reposts,
        "views": views,
    })
    return item


def test_heat_sort_combines_engagement_and_recency():
    older = engagement_item(
        "older", "2026-11-07", likes=1000, replies=200, reposts=100, views=100000
    )
    newer = engagement_item(
        "newer", "2026-11-10", likes=10, replies=1, reposts=0, views=100
    )

    ranked = web_content.sort_items([newer, older], "heat", "2026-11-10")

    assert [item["source_id"] for item in ranked] == ["older", "newer"]
    assert ranked[0]["heat_score"] == 92.5
    assert ranked[0]["heat_breakdown"] == {
        "likes": 25.0,
        "replies": 25.0,
        "reposts": 20.0,
        "views": 15.0,
        "recency": 7.5,
    }
    assert ranked[0]["heat_age_days"] == 3
    assert ranked[1]["heat_breakdown"]["recency"] == 15.0


def test_heat_sort_prefers_newer_item_with_same_engagement():
    older = engagement_item(
        "older", "2026-11-09", likes=100, replies=20, reposts=10, views=1000
    )
    newer = engagement_item(
        "newer", "2026-11-10", likes=100, replies=20, reposts=10, views=1000
    )

    ranked = web_content.sort_items([older, newer], "heat", "2026-11-10")

    assert [item["source_id"] for item in ranked] == ["newer", "older"]
    assert ranked[0]["heat_breakdown"]["recency"] == 15.0
    assert 0 < ranked[1]["heat_breakdown"]["recency"] < 15.0


def test_heat_sort_rewards_replies_reposts_and_views():
    discussed = engagement_item(
        "discussed", "2026-11-10", likes=100, replies=50, reposts=20, views=1000
    )
    quiet = engagement_item(
        "quiet", "2026-11-10", likes=100, replies=0, reposts=0, views=1000
    )
    ranked_discussion = web_content.sort_items([quiet, discussed], "heat", "2026-11-10")
    low_views = engagement_item("low", "2026-11-10", likes=100, views=100)
    high_views = engagement_item("high", "2026-11-10", likes=100, views=10000)
    ranked_views = web_content.sort_items([low_views, high_views], "heat", "2026-11-10")

    assert [item["source_id"] for item in ranked_discussion] == ["discussed", "quiet"]
    assert ranked_discussion[0]["heat_breakdown"]["replies"] == 25.0
    assert ranked_discussion[0]["heat_breakdown"]["reposts"] == 20.0
    assert [item["source_id"] for item in ranked_views] == ["high", "low"]
    assert ranked_views[0]["heat_breakdown"]["views"] == 15.0


def test_heat_sort_handles_missing_metrics_and_dates():
    missing = engagement_item("missing", "")
    recent = engagement_item("recent", "2026-11-10")

    ranked = web_content.sort_items([missing, recent], "heat", "2026-11-10")

    assert [item["source_id"] for item in ranked] == ["recent", "missing"]
    assert ranked[0]["heat_score"] == 15.0
    assert ranked[1]["heat_score"] == 0.0
    assert ranked[1]["heat_age_days"] is None


def test_cli_x_defaults_to_heat_and_writes_explainable_manifest(monkeypatch, tmp_path):
    today = web_content.datetime.date.today()
    older = engagement_item(
        "older",
        (today - web_content.datetime.timedelta(days=3)).isoformat(),
        likes=1000,
        replies=200,
        reposts=100,
        views=100000,
    )
    newer = engagement_item(
        "newer", today.isoformat(), likes=10, replies=1, reposts=0, views=100
    )
    monkeypatch.setattr(web_content, "collect_x", lambda args: ([newer, older], []))

    result = web_content.main([
        "--source", "x", "--query", '"Codex CLI"', "--topic", "Codex CLI",
        "--out", str(tmp_path),
    ])

    assert result == 0
    items = [json.loads(line) for line in (tmp_path / "items.jsonl").read_text().splitlines()]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert [item["source_id"] for item in items] == ["older", "newer"]
    assert manifest["sort"] == "heat"
    assert manifest["heat"]["weights"] == {
        "likes": 25, "replies": 25, "reposts": 20, "views": 15, "recency": 15,
    }
    assert manifest["heat"]["reference_date"] == today.isoformat()
    assert manifest["heat"]["recency_half_life_days"] == 3.0
    assert items[0]["heat_score"] == 92.5
    assert items[0]["heat_breakdown"]["replies"] == 25.0
