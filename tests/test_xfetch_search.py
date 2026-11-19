"""Exercise the actual xfetch search CLI with local HTTP response fixtures."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("twikit")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".agents/skills/xfetch/scripts"))
import xfetch


@pytest.mark.parametrize(("timeline", "errors", "expected_error"), [
    ({}, [{"message": "Internal server error",
           "path": ["search_by_raw_query", "search_timeline", "timeline"]}], "Internal server error"),
    ({}, [], "instructions"),
    ({"timeline": {"instructions": []}}, [], None),
])
def test_search_distinguishes_failed_and_empty_pages(monkeypatch, capsys, timeline, errors, expected_error):
    payload = {"data": {"search_by_raw_query": {"search_timeline": timeline}}, "errors": errors}
    monkeypatch.setattr(xfetch, "require_session", lambda args: ("dummy", "dummy"))
    monkeypatch.setattr(xfetch.httpx.Client, "post", lambda *a, **kw: xfetch.httpx.Response(200, json=payload))
    monkeypatch.setattr(sys, "argv", ["xfetch", "search", "Tesla", "--format", "jsonl"])

    if expected_error:
        with pytest.raises(SystemExit) as exc:
            xfetch.main()
        assert exc.value.code == 1
    else:
        xfetch.main()
    output = capsys.readouterr()
    assert output.out == ""
    if expected_error:
        assert expected_error in output.err
    else:
        assert output.err == ""
