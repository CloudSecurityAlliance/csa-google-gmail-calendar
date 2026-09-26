"""`python -m csa_google_gmail_calendar.mcp` - the documented way to launch the server
(``mcp/__main__.py``, two lines: ``from . import main; main()``). Run via `runpy` rather than
a real subprocess, so coverage tracks it and so the test does not have to wire up subprocess
coverage collection for two lines. `--help` is used because it is the one invocation of
`main()` that is guaranteed to return immediately rather than attempting to run the stdio
server loop."""
import runpy
import sys


def test_dash_m_entry_point_prints_usage_and_returns(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["csa_google_gmail_calendar.mcp", "--help"])
    runpy.run_module("csa_google_gmail_calendar.mcp.__main__", run_name="__main__")
    assert "usage: csa-google-gmail-calendar" in capsys.readouterr().err
