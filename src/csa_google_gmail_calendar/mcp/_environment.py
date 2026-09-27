"""Environment facts for `report_a_problem`, and the one question it could not answer: is this
copy current?

`report_a_problem` reported the installed version and never asked whether it was the latest, so a
careful bug report could be written in detail against something fixed three releases ago - the
reporter's time first, the maintainer's second (#27).

**Where the check runs is the whole design.** Not at startup: a stdio server should not reach the
network because it booted, and a launch that waits on PyPI is a launch that hangs when PyPI is
slow. Not on errors either - an ordinary refusal (policy denied, message not found) is an error
here, and checking a package index on each would be both wrong and noisy. Filing a report is
already a deliberate act, and being current is exactly what matters at that moment, so that is the
only caller that passes `check_pypi=True`.

Everything else here is offline and touches nothing outside this package.
"""
from __future__ import annotations

import itertools
import json
import os
import platform
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .. import __version__

DIST_NAME = "csa-google-gmail-calendar"
_PYPI_URL = f"https://pypi.org/pypi/{DIST_NAME}/json"
# Short on purpose. This runs while somebody waits for a bug report, and a slow index must cost
# them a line saying "could not check" rather than the tool appearing to hang.
_TIMEOUT_SECONDS = 3.0


@dataclass
class Environment:
    server_version: str
    latest_version: str | None      # None = not checked, or could not be checked
    is_outdated: bool | None        # None when unknown - NOT False, which would read as "current"
    python_version: str
    python_implementation: str
    os: str
    architecture: str
    installed_via: str
    upgrade_command: str
    notes: list[str] = field(default_factory=list)


def _installed_via() -> str:
    """How this copy was installed, inferred from where it lives.

    Ported from `csa-google-workspace/_environment.py`, whose docstring carries the fuller
    reasoning and the measurements behind it. The short version: the routes fail differently, so
    the field changes what a maintainer reads a report as. A pipx or uv-tool venv is isolated and
    upgrades cleanly; a shared environment can have another project's pin holding the version
    down, which is its own explanation for a stale report; an editable checkout is somebody's
    working tree that may match no release at all.

    `uv` ADJACENT to `tools`, not two separate `in` checks, because `/home/uv/projects/tools/...`
    is an ordinary path. The two uv layouts differ - POSIX has a `pythonX.Y` level and Windows
    does not - and adjacency is the part common to both.

    **Residual, inherited along with the code:** this answers where the package LIVES, not how it
    was installed, so a `UV_TOOL_DIR` or `PIPX_HOME` pointing somewhere without the giveaway
    segment falls through to `pip (venv)`. Definitive markers exist (`uv-receipt.toml`,
    `uv = <version>` in `pyvenv.cfg`) but both are filesystem reads this module does not take.
    It is a triage hint on a bug report, not something a decision rests on.
    """
    location = os.path.abspath(os.path.dirname(__file__))
    parts = location.replace("\\", "/").lower().split("/")
    if "pipx" in parts:
        return "pipx"
    if any(a == "uv" and b == "tools" for a, b in itertools.pairwise(parts)):
        return "uv tool"
    if any(p in ("site-packages", "dist-packages") for p in parts):
        return "pip (shared environment)" if sys.prefix == sys.base_prefix else "pip (venv)"
    return "editable checkout or source tree"


def _upgrade_command(installed_via: str) -> str:
    """The command THIS install actually needs, not a generic suggestion.

    "You are out of date" is only half an answer; the half that saves anybody time is which of
    four commands to run. An editable checkout gets `git pull`, because upgrading a working tree
    from PyPI would be the wrong advice and quite possibly destructive to whatever is uncommitted
    in it.
    """
    return {
        "pipx": f"pipx upgrade {DIST_NAME}",
        "uv tool": f"uv tool upgrade {DIST_NAME}",
        "editable checkout or source tree": "git pull (this is a working tree, not a release)",
    }.get(installed_via, f"pip install --upgrade '{DIST_NAME}[mcp]'")


def _as_tuple(version: str) -> tuple[int, ...] | None:
    """`"0.4.0"` -> `(0, 4, 0)`, or `None` for anything that is not plainly numeric.

    Deliberately not a full PEP 440 parser and deliberately not a new dependency. The only
    question is "is the index ahead of us", and a version this cannot read becomes *unknown*
    rather than a guess - a pre-release or a local segment should not be reported as "you are
    behind" when it may be the opposite.
    """
    try:
        return tuple(int(part) for part in version.strip().split("."))
    except (ValueError, AttributeError):
        return None


def latest_on_pypi(timeout: float = _TIMEOUT_SECONDS) -> str | None:
    """The newest version on PyPI, or `None` if that could not be established.

    Every failure is `None` and none of them is raised. This runs to add a courtesy line to a bug
    report: an airgapped machine, a proxy, a DNS failure, a 500 from the index or a JSON shape
    that changed must all cost the reader that one line, never the report and never the tool.

    **The URL is a module constant and deliberately NOT a parameter.** The first draft took one,
    for testability, and bandit was right to flag it (B310): a caller-supplied URL reaches
    `urlopen`, which accepts `file://` and every other scheme urllib knows - turning a version
    check into a file-read primitive. The tests never used the parameter (they patch `urlopen`
    itself), so it was a hole opened for a convenience nothing wanted. Removed rather than
    annotated: a `nosec` would have recorded the reasoning without closing the gap.
    """
    try:
        # B310 is about a caller-supplied URL reaching `urlopen`, which would accept `file://`.
        # `_PYPI_URL` is a module constant with a literal https scheme and nothing can influence
        # it - the parameter that COULD have was removed rather than annotated, which is what
        # makes this suppression honest rather than a silenced finding.
        resp = urllib.request.urlopen(_PYPI_URL, timeout=timeout)  # noqa: S310  # nosec B310
        with resp as response:
            body = json.load(response)
        version = body["info"]["version"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) and version else None


def describe_environment(check_pypi: bool = False) -> Environment:
    """The facts a bug report needs. **Offline unless `check_pypi=True`** - see the module
    docstring for why that is a parameter rather than a default."""
    installed_via = _installed_via()
    latest = latest_on_pypi() if check_pypi else None

    outdated: bool | None = None
    if latest is not None:
        here, there = _as_tuple(__version__), _as_tuple(latest)
        if here is not None and there is not None:
            outdated = there > here

    notes: list[str] = []
    if outdated:
        notes.append(f"This is {__version__}; PyPI has {latest}. Upgrade and retry before "
                     f"filing - the problem may already be fixed. Run: "
                     f"{_upgrade_command(installed_via)}")
    elif check_pypi and latest is None:
        # Said out loud rather than left blank. "Could not check" and "you are current" are
        # different facts, and a reader who sees nothing will assume the second.
        notes.append("Could not reach PyPI to check for a newer version, so this report may be "
                     "against an already-fixed release. Worth upgrading before filing.")
    if installed_via.startswith("pip (shared"):
        notes.append("Installed into a shared environment: another project's pin can hold this "
                     f"package at an old version. `uv tool install {DIST_NAME}[mcp]` isolates it.")

    return Environment(
        server_version=__version__,
        latest_version=latest,
        is_outdated=outdated,
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        os=f"{platform.system()} {platform.release()}",
        architecture=platform.machine() or "unknown",
        installed_via=installed_via,
        upgrade_command=_upgrade_command(installed_via),
        notes=notes,
    )
