"""Suite-wide guard: no test may write into the developer's real home directory.

#23 gave `CSA_GGC_ATTACH_DIR` and `CSA_GGC_DOWNLOAD_DIR` defaults under `~`, and a defaulted
root that does not exist is **created**. That is right for a server starting up and wrong for a
test run, and the difference is invisible at the call site - `cli.main([])` in
`test_mcp_cli.py` goes down the serve path and made `~/Documents/CSA-Outbox` on the machine
running the suite, which is how this was found.

Redirecting HOME per-test would work and would have to be remembered by every future test that
touches the serve path, config defaults, or the token cache. Nobody remembers that; the failure
is silent and the evidence (a stray directory) is somewhere nobody looks. So it is done once,
here, for everything.

`USERPROFILE` as well as `HOME`: `os.path.expanduser` reads `HOME` on POSIX and falls back to
`USERPROFILE` on Windows, and this suite runs on both.

A test that genuinely needs the real home can still get it from `pathlib.Path.home()` before
this fixture runs, or opt out by setting the variables itself - but it should say why.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home
