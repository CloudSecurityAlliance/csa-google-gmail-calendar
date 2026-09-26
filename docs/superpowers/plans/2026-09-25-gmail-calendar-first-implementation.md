# csa-google-gmail-calendar: First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A usable MCP server for Gmail reading, composing and sending with attachments, plus Calendar event management including responding to invitations.

**Architecture:** Mirror `csa-google-workspace`'s proven layering — a credential-free `Backend` protocol with `ApiBackend` (real Google) and `FakeBackend` (offline tests) behind a `PolicyBackend` that enforces capabilities at the seam, with the MCP layer as a thin optional delivery surface that never holds policy of its own. The OAuth installed-app flow, capability gating and flavour machinery port from that sibling rather than being rewritten.

**Tech Stack:** Python ≥3.10, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `mcp>=2.1`, `markdownify` + `beautifulsoup4` (body conversion), `pytest`/`ruff`/`mypy`.

**Spec:** `docs/superpowers/specs/2026-09-01-csa-google-gmail-calendar-design.md`

## Global Constraints

- Package name `csa-google-gmail-calendar`, import name `csa_google_gmail_calendar`, MCP server name **`csa-google-gmail-calendar`** (no `-mcp` suffix — csa-zendesk learned this).
- Version starts at `0.1.0` and stays `0.X.Y`; 1.0.0 is claimed deliberately, never reached by increment.
- `requires-python = ">=3.10"`; `requires = ["setuptools>=83"]` (CVE-2026-59890 sdist bypass).
- Licence Apache-2.0; `license-files = ["LICENSE"]`.
- Runtime dependency floors are **lower-bound only**. Dev/CI floors may be raised freely.
- Ship `py.typed`. `mypy` runs over `src` with `check_untyped_defs = true`.
- `ruff` line-length 120, `select = ["E", "F", "W", "I", "B", "UP"]`.
- **Coverage `fail_under = 90`**, branch coverage on. Coverage is a dead-code detector, not a score (`TESTING.md`).
- **No CSA data in this repo, ever** — not in code, tests, fixtures, commit messages, issues or PR bodies. Test fixtures use `example.com` / `example.org` addresses only.
- **Capability default posture** (spec §3): reads and reversible writes on; anything that changes who has access or persists after the agent stops, off.
- **Scope minimisation** (spec §4): request the scopes the *enabled capabilities* need, nothing more.
- Tool naming follows **ADR-001**: a tool name is a claim. No tool called `delete_email`.
- Every tool carries `ToolAnnotations` with `open_world_hint=True` — all results are third-party content.
- Out of scope for this plan: settings, filters, forwarding, delegates, sendAs, CSE/S-MIME, DWD service account, the four permanent-destroy operations, `users.watch`/`stop`, Calendar ACLs.

## Review Focus

Five failure modes the spec implies that no task's happy path exercises. Each has its test named in the owning task.

1. **An attachment path that escapes the allowlist via symlink or `..`** — `~/docs/link-to-ssh-key` resolving outside `CSA_GGC_ATTACH_DIR` must be refused on the *resolved* path, not the supplied one. (Task 5)
2. **Responding to an invitation you are not invited to** — an event with attendees but none marked `self`, and an event with no attendees at all, must refuse with which case it hit rather than silently patching nothing or clobbering attendee zero. (Task 8)
3. **A message with no plain-text part** — Gmail returns HTML-only bodies routinely; a reader that assumes `text/plain` exists returns empty content and looks like an empty mailbox. (Task 4)
4. **A cached token that is valid but one scope short** — enabling a capability later changes the scope set; the refusal must say "re-consent" and not "you are logged out", because the remedy reads differently. (Task 9)
5. **A 5 MB+ attachment, and a message over Gmail's 25 MB ceiling** — simple upload silently truncates or Google 413s; the refusal must name the limit and the actual size. (Task 6)

---

## File Structure

```
src/csa_google_gmail_calendar/
  __init__.py            __version__ = "0.1.0"
  py.typed
  exceptions.py          typed exceptions (AuthError, NotFoundError, AccessError, ApiError,
                         PolicyError, UnsupportedOperation, ConflictError)
  scopes.py              the PRIVILEGE LATTICE (fixes #11) + scopes_for(capabilities)
  policy.py              capability constants, Gate, Policy, PolicyBackend
  backend.py             Backend protocol, ApiBackend, FakeBackend
  _attachments.py        CSA_GGC_ATTACH_DIR allowlist: resolve, verify, read
  _mime.py               RFC 2822 assembly + base64url; size ceilings
  _markdown.py           HTML -> Markdown + codepoint stripping (port from csa-zendesk)
  _untrusted.py          scrub() at the boundary; body defanging
  auth.py                OAuth installed-app flow (port from csa-google-workspace)
  mail.py                Gmail domain layer over Backend
  calendar.py            Calendar domain layer over Backend
  mcp/
    __init__.py  __main__.py  cli.py  server.py
    _auth_flow.py _login.py _desktop.py _success_page.py   (ported)
    _capabilities.py  _config.py  _flavours.py  _logging.py  _schemas.py  _untrusted.py
    _tools/
      __init__.py  _base.py  auth.py  config.py
      mail_read.py  mail_write.py  mail_send.py
      calendar_read.py  calendar_write.py
      demo.py  feedback.py
scripts/inventory.py     MODIFIED: use scopes.py's lattice
tests/                   one module per source module, plus tests/integration/ (gated)
```

---

### Task 1: Scaffold, exceptions, and the scope privilege lattice

Fixes issue #11. This is first because every later task's scope decisions depend on it being right.

**Files:**
- Create: `pyproject.toml`, `src/csa_google_gmail_calendar/__init__.py`, `src/csa_google_gmail_calendar/py.typed`, `src/csa_google_gmail_calendar/exceptions.py`, `src/csa_google_gmail_calendar/scopes.py`
- Modify: `scripts/inventory.py:84`
- Test: `tests/test_scopes.py`, `tests/test_inventory_script.py`

**Interfaces:**
- Produces: `scopes.narrowest(api: str, scopes: list[str]) -> str`; `scopes.RANK: dict[str, int]`; `scopes.UNRANKED: frozenset[str]`; `exceptions.{AuthError,NotFoundError,AccessError,ApiError,PolicyError,UnsupportedOperation,ConflictError}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scopes.py
import pytest
from csa_google_gmail_calendar import scopes

B = "https://www.googleapis.com/auth/"

def test_readonly_beats_modify_for_a_gmail_read():
    """The bug in #11: min(scopes, key=len) picked gmail.modify (12) over gmail.readonly (14)."""
    got = scopes.narrowest("gmail", [
        "https://mail.google.com/", f"{B}gmail.metadata", f"{B}gmail.modify", f"{B}gmail.readonly",
    ])
    assert got == f"{B}gmail.readonly"

def test_bare_calendar_is_the_broadest_not_the_narrowest():
    """Every Calendar row was wrong: bare `calendar` is full access and the shortest string."""
    got = scopes.narrowest("calendar", [
        f"{B}calendar", f"{B}calendar.events", f"{B}calendar.events.readonly", f"{B}calendar.readonly",
    ])
    assert got == f"{B}calendar.events.readonly"

def test_write_method_gets_the_narrowest_write_scope():
    got = scopes.narrowest("calendar", [f"{B}calendar", f"{B}calendar.events", f"{B}calendar.events.owned"])
    assert got == f"{B}calendar.events"

def test_unranked_scopes_cannot_win_on_length():
    """gmail.addons.* and calendar.app.created are excluded BY NAME, not by heuristic."""
    got = scopes.narrowest("gmail", [
        f"{B}gmail.addons.current.message.readonly", f"{B}gmail.readonly", f"{B}gmail.modify",
    ])
    assert got == f"{B}gmail.readonly"

def test_an_entirely_unranked_list_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="no ranked scope"):
        scopes.narrowest("gmail", [f"{B}gmail.addons.current.message.action"])

def test_metadata_is_narrower_than_readonly():
    """gmail.metadata cannot read bodies, so it is strictly less than readonly."""
    got = scopes.narrowest("gmail", [f"{B}gmail.readonly", f"{B}gmail.metadata"])
    assert got == f"{B}gmail.metadata"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scopes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'csa_google_gmail_calendar'`

- [ ] **Step 3: Write the implementation**

```python
# src/csa_google_gmail_calendar/scopes.py
"""The privilege lattice. Issue #11: `min(scopes, key=len)` sorts on string length, which is
unrelated to privilege — bare `calendar` is full access AND the shortest name, so every Calendar
row named the broadest scope as the narrowest, and `gmail.modify` (12 chars) beat
`gmail.readonly` (14) for reads.

Spec §4 makes scope minimisation a requirement and criticises both official servers for
over-declaring. A heuristic cannot deliver that; an explicit ordering can.

RANK is a total order per API, lowest = least privilege. Scopes NOT in RANK are excluded by
name rather than silently winning: the `gmail.addons.*` family is add-on-context-only and
`calendar.app.created` is restricted to app-created calendars, so neither is a scope this
server would request, and neither should be able to outrank one it would.
"""
from __future__ import annotations

_BASE = "https://www.googleapis.com/auth/"

# Least privilege first. The integer is the rank; ties are impossible by construction.
_GMAIL_ORDER = (
    f"{_BASE}gmail.metadata",      # headers and labels; cannot read a body
    f"{_BASE}gmail.readonly",      # read everything, change nothing
    f"{_BASE}gmail.labels",        # label CRUD only
    f"{_BASE}gmail.send",          # put mail on the wire; no mailbox read
    f"{_BASE}gmail.compose",       # draft CRUD (and, per its docs, permanent draft delete)
    f"{_BASE}gmail.insert",        # import/insert
    f"{_BASE}gmail.settings.basic",
    f"{_BASE}gmail.settings.sharing",
    f"{_BASE}gmail.modify",        # the whole mailbox, short of permanent delete
    "https://mail.google.com/",    # everything, including permanent delete
)

_CALENDAR_ORDER = (
    f"{_BASE}calendar.events.public.readonly",
    f"{_BASE}calendar.events.freebusy",
    f"{_BASE}calendar.freebusy",
    f"{_BASE}calendar.settings.readonly",
    f"{_BASE}calendar.calendarlist.readonly",
    f"{_BASE}calendar.events.owned.readonly",
    f"{_BASE}calendar.events.readonly",
    f"{_BASE}calendar.readonly",
    f"{_BASE}calendar.calendarlist",
    f"{_BASE}calendar.events.owned",
    f"{_BASE}calendar.events",
    f"{_BASE}calendar.acls.readonly",
    f"{_BASE}calendar.acls",
    f"{_BASE}calendar.calendars",
    f"{_BASE}calendar",
)

RANK: dict[str, int] = {
    **{s: i for i, s in enumerate(_GMAIL_ORDER)},
    **{s: i for i, s in enumerate(_CALENDAR_ORDER)},
}

# Excluded BY NAME. Not a denylist of things we fear — a statement that these are outside the
# order entirely, because they grant authority in a different dimension (add-on invocation
# context, app-created resources) and cannot be compared to the ones above.
UNRANKED: frozenset[str] = frozenset({
    f"{_BASE}calendar.app.created",
    f"{_BASE}gmail.addons.current.action.compose",
    f"{_BASE}gmail.addons.current.message.action",
    f"{_BASE}gmail.addons.current.message.metadata",
    f"{_BASE}gmail.addons.current.message.readonly",
})

_ORDERS = {"gmail": _GMAIL_ORDER, "calendar": _CALENDAR_ORDER}


def narrowest(api: str, candidates: list[str]) -> str:
    """The least-privilege scope in `candidates`, by the lattice rather than by name length.

    Raises `ValueError` rather than guessing when nothing is ranked. A method reachable only
    through an add-on scope is a method this server does not call, and answering with one
    anyway would put an unrequestable scope into the manifest.
    """
    if api not in _ORDERS:
        raise ValueError(f"no scope order for api {api!r}")
    ranked = [s for s in candidates if s in RANK and s not in UNRANKED]
    if not ranked:
        raise ValueError(
            f"no ranked scope among {len(candidates)} candidate(s) for {api}: "
            f"{sorted(s.rsplit('/', 1)[-1] for s in candidates)}")
    return min(ranked, key=lambda s: RANK[s])
```

Also write `exceptions.py` with the seven exception types, each a subclass of a common
`CsaGoogleError(Exception)`, and `__init__.py` containing only `__version__ = "0.1.0"` plus the
public re-exports. `py.typed` is an empty file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scopes.py -v`
Expected: 6 passed

- [ ] **Step 5: Patch the inventory script and regenerate**

Replace `scripts/inventory.py:84`:

```python
# was: "narrowest_scope": min(scopes, key=len) if scopes else "",
"narrowest_scope": _narrowest_or_blank(api, scopes),
```

```python
def _narrowest_or_blank(api: str, scopes: list[str]) -> str:
    """Blank rather than a guess when the lattice cannot rank anything (issue #11)."""
    if not scopes:
        return ""
    try:
        return narrowest(api, scopes)
    except ValueError:
        return ""
```

- [ ] **Step 6: Write the regression test for the script**

```python
# tests/test_inventory_script.py
import csv, pathlib

def test_no_calendar_row_claims_bare_calendar_is_narrowest():
    """Regression for #11. Bare `calendar` is full access; it is never the narrowest."""
    rows = list(csv.DictReader(open(pathlib.Path("analysis/operation-inventory.csv"))))
    cal = [r for r in rows if r["api"] == "calendar" and r["narrowest_scope"]]
    assert cal, "calendar rows present"
    bad = [r for r in cal if r["narrowest_scope"].endswith("/auth/calendar")]
    assert not bad, f"{len(bad)} calendar rows still name the broadest scope as narrowest"

def test_gmail_reads_do_not_name_a_write_scope():
    rows = list(csv.DictReader(open(pathlib.Path("analysis/operation-inventory.csv"))))
    reads = [r for r in rows
             if r["api"] == "gmail" and r["mutating"].lower() in ("false", "no", "0")
             and r["narrowest_scope"]]
    assert reads
    bad = [r for r in reads if r["narrowest_scope"].endswith(("gmail.modify", "mail.google.com/"))]
    assert not bad, f"{len(bad)} gmail reads name a write scope as narrowest"
```

- [ ] **Step 7: Regenerate the inventory and coverage matrix**

Run: `python scripts/inventory.py && python scripts/coverage.py`
Then: `pytest tests/test_inventory_script.py -v` → 2 passed

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/ tests/test_scopes.py tests/test_inventory_script.py \
        scripts/inventory.py analysis/operation-inventory.csv analysis/coverage-matrix.csv
git commit -m "feat: a privilege lattice, because scope narrowness is not string length (#11)"
```

---

### Task 2: Capability model and the policy seam

**Files:**
- Create: `src/csa_google_gmail_calendar/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `exceptions.PolicyError`
- Produces: capability constants `MAIL_READ`, `MAIL_WRITE`, `MAIL_SEND`, `MAIL_DELETE`, `CALENDAR_READ`, `CALENDAR_WRITE`, `CALENDAR_DELETE`; `ALL_CAPABILITIES: tuple[str, ...]`; `DEFAULT_ENABLED: frozenset[str]`; `Gate(capability: str | None)`; `_GATES: dict[str, Gate]`; `Policy(enabled: frozenset[str])` with `.allows(method: str) -> bool` and `.require(method: str) -> None`; `PolicyBackend(inner: Backend, policy: Policy)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_policy.py
import pytest
from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.exceptions import PolicyError

def test_default_posture_enables_reads_and_reversible_writes():
    assert policy.MAIL_READ in policy.DEFAULT_ENABLED
    assert policy.MAIL_WRITE in policy.DEFAULT_ENABLED
    assert policy.MAIL_SEND in policy.DEFAULT_ENABLED
    assert policy.CALENDAR_READ in policy.DEFAULT_ENABLED
    assert policy.CALENDAR_WRITE in policy.DEFAULT_ENABLED

def test_default_posture_disables_what_persists_or_destroys():
    assert policy.MAIL_DELETE not in policy.DEFAULT_ENABLED
    assert policy.CALENDAR_DELETE not in policy.DEFAULT_ENABLED

def test_every_backend_method_has_a_gate():
    """Fail-closed: a method with no gate is a method nobody decided about."""
    from csa_google_gmail_calendar.backend import Backend
    methods = {m for m in dir(Backend) if not m.startswith("_")}
    assert methods <= set(policy._GATES), f"ungated: {sorted(methods - set(policy._GATES))}"

def test_require_refuses_a_disabled_capability_by_name():
    p = policy.Policy(frozenset({policy.MAIL_READ}))
    with pytest.raises(PolicyError, match="mail.send"):
        p.require("send_message")

def test_refusal_names_the_env_var_that_would_enable_it():
    """DEC-020: a refusal is a negotiation. It says what would change the answer."""
    p = policy.Policy(frozenset({policy.MAIL_READ}))
    with pytest.raises(PolicyError, match="CSA_GGC_CAPABILITIES"):
        p.require("send_message")

def test_policy_backend_refuses_before_the_inner_backend_is_touched():
    calls = []
    class Spy:
        def send_message(self, **kw): calls.append(kw); return {}
    pb = policy.PolicyBackend(Spy(), policy.Policy(frozenset({policy.MAIL_READ})))
    with pytest.raises(PolicyError):
        pb.send_message(to=["a@example.com"], subject="x", body="y")
    assert calls == [], "the gate must fire before the call, not after"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_policy.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar.policy'`

- [ ] **Step 3: Write the implementation**

```python
# src/csa_google_gmail_calendar/policy.py
"""Capabilities, and the seam that enforces them.

The seam is `PolicyBackend`, which wraps a `Backend` and refuses BEFORE delegating. This is
the rule csa-zendesk states as "the library is callable without the server": controls live at
the Backend, never in the MCP layer, so an embedder using the library directly gets the same
refusals a model does.

Default posture (spec §3): reads and reversible writes on; anything that changes who has
access or persists after the agent stops, off. One rule, statable in a sentence, surviving
new methods being added.
"""
from __future__ import annotations

from typing import Any, Callable

from .exceptions import PolicyError

MAIL_READ = "mail.read"
MAIL_WRITE = "mail.write"        # draft CRUD, label application, trash/untrash, spam
MAIL_SEND = "mail.send"          # outbound - Google gives it its own scope
MAIL_DELETE = "mail.delete"      # permanent destroys                      OFF by default
CALENDAR_READ = "calendar.read"
CALENDAR_WRITE = "calendar.write"
CALENDAR_DELETE = "calendar.delete"                                     # OFF by default

ALL_CAPABILITIES: tuple[str, ...] = (
    MAIL_READ, MAIL_WRITE, MAIL_SEND, MAIL_DELETE,
    CALENDAR_READ, CALENDAR_WRITE, CALENDAR_DELETE,
)

# Everything except the two that destroy. `mail.delete` is off per spec §3; `calendar.delete`
# is OUR line, not Google's — no scope separates events.delete from events.patch, so the CI
# scope test cannot verify this one and skips it by name.
DEFAULT_ENABLED: frozenset[str] = frozenset(ALL_CAPABILITIES) - {MAIL_DELETE, CALENDAR_DELETE}

IRREVERSIBLE: frozenset[str] = frozenset({MAIL_SEND, MAIL_DELETE, CALENDAR_DELETE})


class Gate:
    """What a Backend method costs. `capability=None` is a read, decided rather than absent."""
    __slots__ = ("capability",)

    def __init__(self, capability: str | None) -> None:
        self.capability = capability


_R = Gate(None)

_GATES: dict[str, Gate] = {
    # --- mail reads ---
    "search_messages": _R, "get_message": _R, "get_thread": _R, "list_threads": _R,
    "get_attachment": _R, "list_labels": _R, "list_drafts": _R, "get_draft": _R,
    "list_history": _R, "get_profile": _R,
    # --- mail reversible writes ---
    "create_draft": Gate(MAIL_WRITE), "update_draft": Gate(MAIL_WRITE),
    "delete_draft": Gate(MAIL_DELETE),   # permanent by its own docs; spec §3 override
    "modify_message_labels": Gate(MAIL_WRITE), "modify_thread_labels": Gate(MAIL_WRITE),
    "archive_message": Gate(MAIL_WRITE), "archive_thread": Gate(MAIL_WRITE),
    "mark_read": Gate(MAIL_WRITE), "mark_unread": Gate(MAIL_WRITE),
    "trash_message": Gate(MAIL_WRITE), "trash_thread": Gate(MAIL_WRITE),
    "untrash_message": Gate(MAIL_WRITE), "untrash_thread": Gate(MAIL_WRITE),
    "mark_spam": Gate(MAIL_WRITE), "unmark_spam": Gate(MAIL_WRITE),
    "create_label": Gate(MAIL_WRITE),
    # --- outbound ---
    # `send_draft` is MAIL_SEND, not MAIL_WRITE: Google gates it at compose level, and spec §3
    # overrides that deliberately because both it and `send_message` put mail on the wire.
    "send_message": Gate(MAIL_SEND), "send_draft": Gate(MAIL_SEND),
    "reply_message": Gate(MAIL_SEND), "reply_all_message": Gate(MAIL_SEND),
    "forward_message": Gate(MAIL_SEND),
    # --- calendar ---
    "list_calendars": _R, "get_calendar": _R, "list_events": _R, "get_event": _R,
    "query_freebusy": _R,
    "create_event": Gate(CALENDAR_WRITE), "update_event": Gate(CALENDAR_WRITE),
    "respond_to_event": Gate(CALENDAR_WRITE),
    "delete_event": Gate(CALENDAR_DELETE),
}


class Policy:
    def __init__(self, enabled: frozenset[str] = DEFAULT_ENABLED) -> None:
        unknown = set(enabled) - set(ALL_CAPABILITIES)
        if unknown:
            raise ValueError(f"unknown capability/capabilities: {sorted(unknown)}")
        self.enabled = frozenset(enabled)

    def allows(self, method: str) -> bool:
        gate = _GATES.get(method)
        if gate is None:
            return False          # fail closed: an ungated method is one nobody decided about
        return gate.capability is None or gate.capability in self.enabled

    def require(self, method: str) -> None:
        gate = _GATES.get(method)
        if gate is None:
            raise PolicyError(
                f"{method!r} has no gate, so this server will not call it. This is a bug: "
                f"every Backend method must be declared in policy._GATES.")
        if gate.capability is None or gate.capability in self.enabled:
            return
        raise PolicyError(
            f"{method} needs the {gate.capability!r} capability, which is not enabled. "
            f"Enabled: {sorted(self.enabled) or 'none'}. To enable it, set "
            f"CSA_GGC_CAPABILITIES to a comma-separated list including {gate.capability!r}."
            + (" This capability is off by default because the action it permits cannot be "
               "undone." if gate.capability in IRREVERSIBLE else ""))


class PolicyBackend:
    """Refuses before delegating. Attribute access is intercepted so a Backend method added
    later is gated by construction rather than by somebody remembering to wrap it."""

    def __init__(self, inner: Any, policy: Policy) -> None:
        self._inner = inner
        self._policy = policy

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)
        inner_attr = getattr(self._inner, name)
        if not callable(inner_attr):
            return inner_attr

        def gated(*args: Any, **kwargs: Any) -> Any:
            self._policy.require(name)
            return inner_attr(*args, **kwargs)

        return gated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_policy.py -v`
Expected: 6 passed (`test_every_backend_method_has_a_gate` will fail until Task 3 — mark it `xfail(strict=True)` here and remove the marker in Task 3 Step 6)

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/policy.py tests/test_policy.py
git commit -m "feat: capabilities gate at the Backend seam, so the library refuses without the server"
```

---

### Task 3: Backend protocol and FakeBackend

**Files:**
- Create: `src/csa_google_gmail_calendar/backend.py`
- Modify: `tests/test_policy.py` (remove the xfail marker)
- Test: `tests/test_backend_contract.py`

**Interfaces:**
- Consumes: `exceptions.NotFoundError`
- Produces: `Backend` (Protocol, every method in `policy._GATES`); `FakeBackend(messages=…, events=…)` implementing it in memory

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend_contract.py
import pytest
from csa_google_gmail_calendar.backend import Backend, FakeBackend
from csa_google_gmail_calendar.exceptions import NotFoundError

def test_fake_implements_every_protocol_method():
    """Any method on the Protocol the Fake lacks is a hole the offline suite cannot see."""
    wanted = {m for m in dir(Backend) if not m.startswith("_")}
    missing = [m for m in wanted if not callable(getattr(FakeBackend, m, None))]
    assert not missing, f"FakeBackend is missing {missing}"

def test_get_message_returns_the_seeded_message():
    fake = FakeBackend(messages={"m1": {"id": "m1", "snippet": "hello",
                                        "payload": {"headers": [{"name": "Subject", "value": "Hi"}]}}})
    assert fake.get_message(message_id="m1")["snippet"] == "hello"

def test_unknown_id_raises_notfound_not_keyerror():
    """A KeyError becomes an UnexpectedToolError whose message the SDK suppresses."""
    with pytest.raises(NotFoundError, match="nope"):
        FakeBackend().get_message(message_id="nope")

def test_archive_removes_inbox_and_nothing_else():
    fake = FakeBackend(messages={"m1": {"id": "m1", "labelIds": ["INBOX", "UNREAD", "IMPORTANT"]}})
    fake.archive_message(message_id="m1")
    assert fake.messages["m1"]["labelIds"] == ["UNREAD", "IMPORTANT"]

def test_archive_is_idempotent():
    fake = FakeBackend(messages={"m1": {"id": "m1", "labelIds": ["UNREAD"]}})
    fake.archive_message(message_id="m1")
    assert fake.messages["m1"]["labelIds"] == ["UNREAD"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend_contract.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar.backend'`

- [ ] **Step 3: Write the Protocol**

Define `class Backend(Protocol)` with one method per key in `policy._GATES`, all keyword-only,
each returning `dict[str, Any]` or `list[dict[str, Any]]`. Signatures used by later tasks:

```python
class Backend(Protocol):
    # reads
    def search_messages(self, *, query: str, limit: int = 25,
                        page_token: str | None = None) -> dict[str, Any]: ...
    def get_message(self, *, message_id: str, fmt: str = "full") -> dict[str, Any]: ...
    def get_thread(self, *, thread_id: str, fmt: str = "full") -> dict[str, Any]: ...
    def list_threads(self, *, query: str | None = None, limit: int = 25) -> dict[str, Any]: ...
    def get_attachment(self, *, message_id: str, attachment_id: str) -> dict[str, Any]: ...
    def list_labels(self) -> list[dict[str, Any]]: ...
    def list_drafts(self, *, limit: int = 25) -> list[dict[str, Any]]: ...
    def get_draft(self, *, draft_id: str) -> dict[str, Any]: ...
    def list_history(self, *, start_history_id: str) -> dict[str, Any]: ...
    def get_profile(self) -> dict[str, Any]: ...
    # reversible writes
    def create_draft(self, *, raw: str) -> dict[str, Any]: ...
    def update_draft(self, *, draft_id: str, raw: str) -> dict[str, Any]: ...
    def delete_draft(self, *, draft_id: str) -> dict[str, Any]: ...
    def modify_message_labels(self, *, message_id: str, add: list[str] | None = None,
                              remove: list[str] | None = None) -> dict[str, Any]: ...
    def modify_thread_labels(self, *, thread_id: str, add: list[str] | None = None,
                             remove: list[str] | None = None) -> dict[str, Any]: ...
    def archive_message(self, *, message_id: str) -> dict[str, Any]: ...
    def archive_thread(self, *, thread_id: str) -> dict[str, Any]: ...
    def mark_read(self, *, message_id: str) -> dict[str, Any]: ...
    def mark_unread(self, *, message_id: str) -> dict[str, Any]: ...
    def trash_message(self, *, message_id: str) -> dict[str, Any]: ...
    def trash_thread(self, *, thread_id: str) -> dict[str, Any]: ...
    def untrash_message(self, *, message_id: str) -> dict[str, Any]: ...
    def untrash_thread(self, *, thread_id: str) -> dict[str, Any]: ...
    def mark_spam(self, *, message_id: str) -> dict[str, Any]: ...
    def unmark_spam(self, *, message_id: str) -> dict[str, Any]: ...
    def create_label(self, *, name: str) -> dict[str, Any]: ...
    # outbound
    def send_message(self, *, raw: str) -> dict[str, Any]: ...
    def send_draft(self, *, draft_id: str) -> dict[str, Any]: ...
    def reply_message(self, *, raw: str, thread_id: str) -> dict[str, Any]: ...
    def reply_all_message(self, *, raw: str, thread_id: str) -> dict[str, Any]: ...
    def forward_message(self, *, raw: str) -> dict[str, Any]: ...
    # calendar
    def list_calendars(self) -> list[dict[str, Any]]: ...
    def get_calendar(self, *, calendar_id: str) -> dict[str, Any]: ...
    def list_events(self, *, calendar_id: str = "primary", time_min: str | None = None,
                    time_max: str | None = None, query: str | None = None,
                    limit: int = 25) -> dict[str, Any]: ...
    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]: ...
    def query_freebusy(self, *, time_min: str, time_max: str,
                       calendar_ids: list[str]) -> dict[str, Any]: ...
    def create_event(self, *, calendar_id: str, body: dict[str, Any],
                     send_updates: str = "all") -> dict[str, Any]: ...
    def update_event(self, *, calendar_id: str, event_id: str, body: dict[str, Any],
                     send_updates: str = "all",
                     etag: str | None = None) -> dict[str, Any]: ...
    def respond_to_event(self, *, calendar_id: str, event_id: str, response: str,
                         comment: str | None = None) -> dict[str, Any]: ...
    def delete_event(self, *, calendar_id: str, event_id: str,
                     send_updates: str = "all") -> dict[str, Any]: ...
```

Then `FakeBackend` implementing all of it over `self.messages`, `self.threads`, `self.drafts`,
`self.labels`, `self.events`, `self.calendars`, `self.sent` (a list every outbound method
appends to, so tests can assert what left). Every lookup miss raises `NotFoundError(the_id)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backend_contract.py -v` → 5 passed

- [ ] **Step 5: Remove the xfail in test_policy.py and re-run**

Run: `pytest tests/test_policy.py tests/test_backend_contract.py -v` → 11 passed

- [ ] **Step 6: Commit**

```bash
git add src/csa_google_gmail_calendar/backend.py tests/test_backend_contract.py tests/test_policy.py
git commit -m "feat: the Backend protocol and an in-memory double the whole offline suite runs on"
```

---

### Task 4: Reading a message — the MIME walk and the HTML-only case

Review Focus #3 lives here.

**Files:**
- Create: `src/csa_google_gmail_calendar/_markdown.py`, `src/csa_google_gmail_calendar/mail.py`
- Test: `tests/test_message_parsing.py`

**Interfaces:**
- Consumes: `backend.Backend`
- Produces: `mail.Mail(backend)` with `.read_message(message_id) -> ParsedMessage`;
  `mail.ParsedMessage` (dataclass: `id`, `thread_id`, `subject`, `sender`, `to`, `cc`, `date`,
  `body_markdown`, `body_source` ∈ `{"text/plain","text/html","none"}`, `attachments: list[AttachmentRef]`,
  `label_ids`, `transformations: list[str]`);
  `mail.AttachmentRef` (dataclass: `attachment_id`, `filename`, `mime_type`, `size_bytes`);
  `_markdown.to_markdown(html) -> tuple[str, list[str]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_message_parsing.py
import base64
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.mail import Mail

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()

def _msg(payload, mid="m1"):
    return {"id": mid, "threadId": "t1", "labelIds": ["INBOX"], "payload": payload}

def test_plain_text_body_is_read_straight_through():
    p = {"mimeType": "text/plain", "headers": [{"name": "Subject", "value": "Hi"}],
         "body": {"data": _b64("hello there")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_markdown == "hello there"
    assert m.body_source == "text/plain"

def test_html_only_message_is_converted_not_dropped():
    """REVIEW FOCUS #3. Gmail returns HTML-only bodies routinely. A reader that assumes
    text/plain exists returns nothing and the mailbox looks empty."""
    p = {"mimeType": "text/html", "headers": [{"name": "Subject", "value": "Hi"}],
         "body": {"data": _b64("<p>hello <b>there</b></p>")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "hello" in m.body_markdown and "there" in m.body_markdown
    assert m.body_source == "text/html"

def test_multipart_alternative_prefers_plain_over_html():
    p = {"mimeType": "multipart/alternative", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("plain version")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_markdown == "plain version"

def test_nested_multipart_related_inside_alternative_is_walked():
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "multipart/related", "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>deep</p>")}}]}]}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "deep" in m.body_markdown

def test_a_message_with_no_body_part_at_all_says_so():
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "application/pdf", "filename": "x.pdf",
         "body": {"attachmentId": "a1", "size": 10}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.body_source == "none"
    assert m.body_markdown == ""
    assert len(m.attachments) == 1

def test_attachments_are_listed_with_id_name_type_and_size():
    p = {"mimeType": "multipart/mixed", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("see attached")}},
        {"mimeType": "application/pdf", "filename": "report.pdf",
         "body": {"attachmentId": "att-1", "size": 4096}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert [(a.filename, a.mime_type, a.size_bytes) for a in m.attachments] \
        == [("report.pdf", "application/pdf", 4096)]
    assert m.attachments[0].attachment_id == "att-1"

def test_inline_image_without_a_filename_is_not_reported_as_an_attachment():
    """A cid: image is part of the body, not something a person attached."""
    p = {"mimeType": "multipart/related", "headers": [], "parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<p>hi</p>")}},
        {"mimeType": "image/png", "filename": "",
         "headers": [{"name": "Content-Disposition", "value": "inline"}],
         "body": {"attachmentId": "img-1", "size": 99}}]}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert m.attachments == []

def test_body_conversion_is_disclosed_as_a_transformation():
    """DEC-021: disclose what you changed. A converted body is not the original."""
    p = {"mimeType": "text/html", "headers": [], "body": {"data": _b64("<p>x</p>")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert any("html" in t for t in m.transformations)

def test_bidi_override_codepoints_are_stripped_and_counted():
    """A RIGHT-TO-LEFT OVERRIDE can make a body render as text it does not contain."""
    p = {"mimeType": "text/plain", "headers": [],
         "body": {"data": _b64("safe‮txt.exe")}}
    m = Mail(FakeBackend(messages={"m1": _msg(p)})).read_message("m1")
    assert "‮" not in m.body_markdown
    assert any("bidi" in t for t in m.transformations)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_message_parsing.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar.mail'`

- [ ] **Step 3: Write `_markdown.py`**

Port `src/csa_zendesk/_markdown.py` from `../csa-zendesk` verbatim where it applies. It already
provides `to_markdown(html) -> (markdown, rules_fired)` over `markdownify` + `beautifulsoup4`,
a hidden-element pre-pass that surfaces hidden text rather than dropping it, and `_STRIP_RULES`
naming three classes: `codepoint/bidi-override`, `codepoint/byte-order-mark`,
`codepoint/control-character`. Change only the module docstring's project name.

- [ ] **Step 4: Write the MIME walk in `mail.py`**

```python
_BODY_PREFERENCE = ("text/plain", "text/html")

def _walk(part: dict[str, Any]):
    """Depth-first over the MIME tree. Gmail nests multipart/related inside
    multipart/alternative inside multipart/mixed routinely, and a one-level scan misses
    the body entirely on exactly those messages."""
    yield part
    for child in part.get("parts") or ():
        yield from _walk(child)


def _is_attachment(part: dict[str, Any]) -> bool:
    """A filename AND an attachmentId. An inline cid: image has an attachmentId and an empty
    filename — it is part of the body, not something a person attached, and listing it makes
    every HTML newsletter look like it carried three files."""
    return bool(part.get("filename")) and bool((part.get("body") or {}).get("attachmentId"))


def _decode(body: dict[str, Any]) -> str:
    data = body.get("data")
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)     # Gmail strips base64url padding
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
```

`read_message` then: fetch, walk once collecting body candidates by mime type and attachment
refs, pick by `_BODY_PREFERENCE`, convert HTML through `to_markdown`, run the codepoint strip
over plain text too, and record every transformation applied in `transformations`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_message_parsing.py -v` → 9 passed

- [ ] **Step 6: Commit**

```bash
git add src/csa_google_gmail_calendar/_markdown.py src/csa_google_gmail_calendar/mail.py \
        tests/test_message_parsing.py
git commit -m "feat: walk the whole MIME tree, because the body is not where a one-level scan looks"
```

---

### Task 5: The attachment allowlist

Review Focus #1 lives here. This is the security-critical task of the plan.

**Files:**
- Create: `src/csa_google_gmail_calendar/_attachments.py`
- Test: `tests/test_attachment_allowlist.py`

**Interfaces:**
- Produces: `_attachments.AttachmentPolicy(root: str | None)` with
  `.resolve(path: str) -> pathlib.Path` (raises `PolicyError`) and `.read(path: str) -> tuple[bytes, str]`
  returning `(content, filename)`; `_attachments.from_env() -> AttachmentPolicy`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_attachment_allowlist.py
import os, pathlib, pytest
from csa_google_gmail_calendar._attachments import AttachmentPolicy, from_env
from csa_google_gmail_calendar.exceptions import PolicyError

@pytest.fixture
def root(tmp_path):
    d = tmp_path / "attach"; d.mkdir()
    (d / "ok.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "secret.txt").write_bytes(b"not for sending")
    return d

def test_a_file_inside_the_root_resolves(root):
    p = AttachmentPolicy(str(root)).resolve("ok.pdf")
    assert p.name == "ok.pdf"

def test_absolute_path_inside_the_root_resolves(root):
    assert AttachmentPolicy(str(root)).resolve(str(root / "ok.pdf")).name == "ok.pdf"

def test_dotdot_escape_is_refused(root):
    """REVIEW FOCUS #1."""
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("../secret.txt")

def test_symlink_pointing_outside_is_refused_on_the_resolved_path(root):
    """REVIEW FOCUS #1. The check must run on the RESOLVED path. Checking the supplied
    string would pass this — the link is inside the root and its target is not."""
    (root / "innocent.txt").symlink_to(root.parent / "secret.txt")
    with pytest.raises(PolicyError, match="outside"):
        AttachmentPolicy(str(root)).resolve("innocent.txt")

def test_symlink_pointing_inside_is_allowed(root):
    (root / "alias.pdf").symlink_to(root / "ok.pdf")
    assert AttachmentPolicy(str(root)).resolve("alias.pdf").name == "ok.pdf"

def test_a_root_that_is_itself_a_symlink_still_works(root, tmp_path):
    """The root is resolved too, or every path under a symlinked root looks like an escape."""
    link = tmp_path / "via-link"; link.symlink_to(root)
    assert AttachmentPolicy(str(link)).resolve("ok.pdf").name == "ok.pdf"

def test_unconfigured_root_refuses_and_names_the_variable():
    """DEC-020 and the csa-skilljar idiom: it may be one environment variable away."""
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        AttachmentPolicy(None).resolve("anything.pdf")

def test_a_directory_is_not_an_attachment(root):
    (root / "sub").mkdir()
    with pytest.raises(PolicyError, match="not a regular file"):
        AttachmentPolicy(str(root)).resolve("sub")

def test_a_missing_file_says_missing_not_outside(root):
    """Two different problems. 'outside the allowlist' for a file that does not exist sends
    the user looking for a permissions problem they do not have."""
    with pytest.raises(PolicyError, match="does not exist"):
        AttachmentPolicy(str(root)).resolve("nope.pdf")

def test_tilde_is_expanded(root, monkeypatch):
    monkeypatch.setenv("HOME", str(root.parent))
    assert AttachmentPolicy(str(root)).resolve("~/attach/ok.pdf").name == "ok.pdf"

def test_from_env_reads_the_variable(root, monkeypatch):
    monkeypatch.setenv("CSA_GGC_ATTACH_DIR", str(root))
    assert from_env().resolve("ok.pdf").name == "ok.pdf"

def test_from_env_with_no_variable_is_a_policy_that_refuses_everything(monkeypatch):
    monkeypatch.delenv("CSA_GGC_ATTACH_DIR", raising=False)
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        from_env().resolve("x.pdf")

def test_read_returns_bytes_and_the_basename(root):
    content, name = AttachmentPolicy(str(root)).read("ok.pdf")
    assert content == b"%PDF-1.4 fake" and name == "ok.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_attachment_allowlist.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar._attachments'`

- [ ] **Step 3: Write the implementation**

```python
# src/csa_google_gmail_calendar/_attachments.py
"""Which local files may be attached to outgoing mail.

`send_message(attachments=[...])` takes a path, which makes it a file-read primitive wearing
an innocuous name. The bound is a single configured directory, and it is checked on the
**resolved** path: a symlink inside the root pointing at `~/.ssh/id_rsa` is inside the root by
its own name and outside it in every way that matters.

Unset means attachments are off, not unrestricted. That is the default-posture rule from spec
§3 applied to the filesystem, and the refusal names the variable — a capability that is one
environment variable away should say so rather than look broken (the csa-skilljar idiom).
"""
from __future__ import annotations

import os
import pathlib

from .exceptions import PolicyError

ENV_VAR = "CSA_GGC_ATTACH_DIR"


class AttachmentPolicy:
    def __init__(self, root: str | None) -> None:
        # Resolved at construction, once. A root that is itself a symlink (common: a
        # ~/Documents that points into a synced volume) would otherwise make every path
        # under it compare as an escape.
        self.root = pathlib.Path(os.path.expanduser(root)).resolve() if root else None

    def resolve(self, path: str) -> pathlib.Path:
        if self.root is None:
            raise PolicyError(
                f"attachments are disabled: no attachment directory is configured. Set "
                f"{ENV_VAR} to a directory this server may read files from, and only files "
                f"under it can be attached.")
        candidate = pathlib.Path(os.path.expanduser(path))
        if not candidate.is_absolute():
            candidate = self.root / candidate
        # strict=False so a MISSING file reaches the readable error below rather than
        # raising OSError from resolve() itself.
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise PolicyError(
                f"{path!r} resolves to a location outside the attachment directory "
                f"({self.root}). Only files under that directory can be attached.")
        if not resolved.exists():
            raise PolicyError(f"{path!r} does not exist (looked at {resolved}).")
        if not resolved.is_file():
            raise PolicyError(f"{path!r} is not a regular file.")
        return resolved

    def read(self, path: str) -> tuple[bytes, str]:
        p = self.resolve(path)
        return p.read_bytes(), p.name


def from_env() -> AttachmentPolicy:
    return AttachmentPolicy(os.environ.get(ENV_VAR) or None)
```

`Path.is_relative_to` needs Python ≥3.9; the floor is 3.10, so it is available.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_attachment_allowlist.py -v` → 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/_attachments.py tests/test_attachment_allowlist.py
git commit -m "feat: an attachment path is checked where it resolves, not where it points"
```

---

### Task 6: MIME assembly and the size ceilings

Review Focus #5 lives here.

**Files:**
- Create: `src/csa_google_gmail_calendar/_mime.py`
- Test: `tests/test_mime_assembly.py`

**Interfaces:**
- Consumes: `_attachments.AttachmentPolicy`
- Produces: `_mime.build(to, subject, body, *, cc=None, bcc=None, from_addr=None, attachments=None, attach_policy=None, in_reply_to=None, references=None, html_body=None) -> str` (base64url, unpadded-safe); `_mime.SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024`; `_mime.MESSAGE_LIMIT = 25 * 1024 * 1024`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mime_assembly.py
import base64, email, pytest
from csa_google_gmail_calendar import _mime
from csa_google_gmail_calendar._attachments import AttachmentPolicy
from csa_google_gmail_calendar.exceptions import PolicyError

def _parse(raw_b64: str):
    return email.message_from_bytes(base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4)))

def test_a_plain_message_round_trips():
    msg = _parse(_mime.build(["a@example.com"], "Subject here", "Body here"))
    assert msg["To"] == "a@example.com" and msg["Subject"] == "Subject here"
    assert "Body here" in msg.get_content()

def test_multiple_recipients_are_comma_joined():
    msg = _parse(_mime.build(["a@example.com", "b@example.org"], "s", "b"))
    assert msg["To"] == "a@example.com, b@example.org"

def test_cc_is_set_and_bcc_is_also_a_header():
    """Gmail strips Bcc on send. It must still be present, or the recipient never gets it."""
    msg = _parse(_mime.build(["a@example.com"], "s", "b",
                             cc=["c@example.com"], bcc=["d@example.com"]))
    assert msg["Cc"] == "c@example.com" and msg["Bcc"] == "d@example.com"

def test_reply_headers_are_set_so_the_thread_holds_together():
    msg = _parse(_mime.build(["a@example.com"], "Re: s", "b",
                             in_reply_to="<x@mail.example.com>",
                             references="<w@mail.example.com> <x@mail.example.com>"))
    assert msg["In-Reply-To"] == "<x@mail.example.com>"
    assert "<w@mail.example.com>" in msg["References"]

def test_an_attachment_becomes_a_part_with_its_filename(tmp_path):
    d = tmp_path / "a"; d.mkdir(); (d / "r.pdf").write_bytes(b"%PDF-1.4 x")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["r.pdf"],
                             attach_policy=AttachmentPolicy(str(d))))
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert names == ["r.pdf"]

def test_attachment_mime_type_is_guessed_from_the_extension(tmp_path):
    d = tmp_path / "a"; d.mkdir(); (d / "r.pdf").write_bytes(b"%PDF-1.4 x")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["r.pdf"],
                             attach_policy=AttachmentPolicy(str(d))))
    assert next(msg.iter_attachments()).get_content_type() == "application/pdf"

def test_an_unknown_extension_falls_back_to_octet_stream(tmp_path):
    d = tmp_path / "a"; d.mkdir(); (d / "x.zzz").write_bytes(b"data")
    msg = _parse(_mime.build(["a@example.com"], "s", "b", attachments=["x.zzz"],
                             attach_policy=AttachmentPolicy(str(d))))
    assert next(msg.iter_attachments()).get_content_type() == "application/octet-stream"

def test_attachments_without_a_policy_are_refused():
    with pytest.raises(PolicyError, match="CSA_GGC_ATTACH_DIR"):
        _mime.build(["a@example.com"], "s", "b", attachments=["x.pdf"])

def test_over_the_message_ceiling_is_refused_with_both_numbers(tmp_path):
    """REVIEW FOCUS #5. Google 413s; the refusal must say how big it was and what the cap is."""
    d = tmp_path / "a"; d.mkdir()
    (d / "big.bin").write_bytes(b"\0" * (26 * 1024 * 1024))
    with pytest.raises(PolicyError, match=r"25|26"):
        _mime.build(["a@example.com"], "s", "b", attachments=["big.bin"],
                    attach_policy=AttachmentPolicy(str(d)))

def test_base64_overhead_counts_toward_the_ceiling(tmp_path):
    """A 19 MB file is ~25.3 MB once base64'd. Checking the file size alone passes it
    and Google then rejects the send, which is a worse place to find out."""
    d = tmp_path / "a"; d.mkdir()
    (d / "big.bin").write_bytes(b"\0" * (19 * 1024 * 1024))
    with pytest.raises(PolicyError, match="25"):
        _mime.build(["a@example.com"], "s", "b", attachments=["big.bin"],
                    attach_policy=AttachmentPolicy(str(d)))

def test_an_html_alternative_produces_a_multipart_alternative():
    msg = _parse(_mime.build(["a@example.com"], "s", "plain", html_body="<p>rich</p>"))
    assert msg.get_content_type() == "multipart/alternative"

def test_result_is_urlsafe_base64_with_no_plus_or_slash():
    raw = _mime.build(["a@example.com"], "s", "b" * 500)
    assert "+" not in raw and "/" not in raw
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mime_assembly.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar._mime'`

- [ ] **Step 3: Write the implementation**

```python
# src/csa_google_gmail_calendar/_mime.py
"""RFC 2822 assembly. There is no attachment upload endpoint (issue #8) — an attachment is a
part of a message body that rides along on drafts.create / drafts.update / messages.send, so
this is where "attachment support" actually lives.

The ceiling is checked on the ENCODED size. base64 costs 4/3, so a 19 MB file is 25.3 MB on
the wire and passes a check written against the file size. Google then 413s, which is a worse
place to discover it than here.
"""
from __future__ import annotations

import base64
import mimetypes
from email.message import EmailMessage

from ._attachments import AttachmentPolicy
from .exceptions import PolicyError

SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024     # above this Google wants a resumable upload
MESSAGE_LIMIT = 25 * 1024 * 1024          # Gmail's total message ceiling


def build(to, subject, body, *, cc=None, bcc=None, from_addr=None, attachments=None,
          attach_policy: AttachmentPolicy | None = None, in_reply_to=None,
          references=None, html_body=None) -> str:
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # Gmail strips this before delivery. It must be present or the blind recipient
        # is simply not a recipient.
        msg["Bcc"] = ", ".join(bcc)
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for path in attachments or ():
        if attach_policy is None:
            raise PolicyError(
                "attachments are disabled: no attachment directory is configured. Set "
                "CSA_GGC_ATTACH_DIR to a directory this server may read files from.")
        content, filename = attach_policy.read(path)
        guessed, _ = mimetypes.guess_type(filename)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    raw = msg.as_bytes()
    encoded = base64.urlsafe_b64encode(raw)
    if len(encoded) > MESSAGE_LIMIT:
        raise PolicyError(
            f"the assembled message is {len(encoded) / 1024 / 1024:.1f} MB once encoded, over "
            f"Gmail's {MESSAGE_LIMIT // 1024 // 1024} MB limit. base64 encoding adds about a "
            f"third, so the underlying files total {len(raw) / 1024 / 1024:.1f} MB. Send a "
            f"link instead, or split the attachments across messages.")
    return encoded.decode()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mime_assembly.py -v` → 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/_mime.py tests/test_mime_assembly.py
git commit -m "feat: assemble the message, and count the ceiling where base64 has already been paid"
```

---

### Task 7: ApiBackend — the real Google calls

**Files:**
- Modify: `src/csa_google_gmail_calendar/backend.py` (add `ApiBackend`)
- Test: `tests/test_api_backend.py`

**Interfaces:**
- Consumes: `Backend` protocol
- Produces: `ApiBackend(gmail_service, calendar_service)` implementing `Backend`; `ApiBackend.from_credentials(creds) -> ApiBackend`

- [ ] **Step 1: Write the failing test**

Tests use a recording double for the discovery client (`service.users().messages().get(...).execute()`),
asserting the **arguments** reach Google correctly — the thing a FakeBackend cannot check.

```python
# tests/test_api_backend.py
import pytest
from csa_google_gmail_calendar.backend import ApiBackend
from csa_google_gmail_calendar.exceptions import NotFoundError, AccessError

class _Req:
    def __init__(self, rec, name, kwargs, result=None, error=None):
        self.headers = {}; self._rec = rec; self._name = name
        self._kwargs = kwargs; self._result = result; self._error = error
    def execute(self):
        self._rec.append((self._name, self._kwargs, dict(self.headers)))
        if self._error: raise self._error
        return self._result if self._result is not None else {}

class _Chain:
    """Records the full call path so a test can assert users().messages().modify() args."""
    def __init__(self, rec, path="", results=None):
        self._rec, self._path, self._results = rec, path, results or {}
    def __getattr__(self, name):
        def call(**kwargs):
            path = f"{self._path}.{name}".lstrip(".")
            if kwargs or path.count(".") >= 2:
                return _Req(self._rec, path, kwargs, self._results.get(path))
            return _Chain(self._rec, path, self._results)
        return call

def test_search_passes_q_and_maxResults_to_gmail():
    rec = []
    ab = ApiBackend(_Chain(rec), _Chain([]))
    ab.search_messages(query="from:a@example.com", limit=7)
    name, kwargs, _ = rec[0]
    assert kwargs["q"] == "from:a@example.com" and kwargs["maxResults"] == 7
    assert kwargs["userId"] == "me"

def test_archive_removes_inbox_only():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).archive_message(message_id="m1")
    _, kwargs, _ = rec[0]
    assert kwargs["body"] == {"removeLabelIds": ["INBOX"]}

def test_mark_read_removes_unread():
    rec = []
    ApiBackend(_Chain(rec), _Chain([])).mark_read(message_id="m1")
    assert rec[0][1]["body"] == {"removeLabelIds": ["UNREAD"]}

def test_update_event_sends_if_match_when_an_etag_is_given():
    """Optimistic concurrency. Without it, two concurrent patches silently lose one."""
    rec = []
    ApiBackend(_Chain([]), _Chain(rec)).update_event(
        calendar_id="primary", event_id="e1", body={"summary": "x"}, etag='"abc"')
    _, _, headers = rec[0]
    assert headers["If-Match"] == '"abc"'

def test_update_event_omits_if_match_when_no_etag():
    rec = []
    ApiBackend(_Chain([]), _Chain(rec)).update_event(
        calendar_id="primary", event_id="e1", body={"summary": "x"})
    assert "If-Match" not in rec[0][2]

def test_a_404_becomes_notfound():
    from googleapiclient.errors import HttpError
    class R: status = 404; reason = "Not Found"
    err = HttpError(R(), b'{"error":{"message":"Not Found"}}')
    class Boom(_Chain):
        def __getattr__(self, name):
            def call(**kw):
                path = f"{self._path}.{name}".lstrip(".")
                if kw or path.count(".") >= 2:
                    return _Req([], path, kw, error=err)
                return Boom([], path)
            return call
    with pytest.raises(NotFoundError):
        ApiBackend(Boom([]), _Chain([])).get_message(message_id="gone")

def test_a_403_becomes_accesserror():
    from googleapiclient.errors import HttpError
    class R: status = 403; reason = "Forbidden"
    err = HttpError(R(), b'{"error":{"message":"Insufficient Permission"}}')
    class Boom(_Chain):
        def __getattr__(self, name):
            def call(**kw):
                path = f"{self._path}.{name}".lstrip(".")
                if kw or path.count(".") >= 2:
                    return _Req([], path, kw, error=err)
                return Boom([], path)
            return call
    with pytest.raises(AccessError):
        ApiBackend(Boom([]), _Chain([])).get_message(message_id="m1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_backend.py -v`
Expected: FAIL — `cannot import name 'ApiBackend'`

- [ ] **Step 3: Write `ApiBackend`**

Every method wraps its call in `_translate`, which maps `HttpError` by status:
404 → `NotFoundError`, 403 → `AccessError`, 401 → `AuthError`, 409 → `ConflictError`,
412 → `ConflictError` (etag mismatch, with a message saying the event changed underneath),
429/5xx → `ApiError` with a retry note, other 4xx → `ApiError`.

`update_event` sets `If-Match` on the request object before `.execute()`:

```python
req = self._cal.events().patch(calendarId=calendar_id, eventId=event_id,
                               body=body, sendUpdates=send_updates)
if etag:
    req.headers["If-Match"] = etag
return _translate(req.execute)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_backend.py -v` → 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/backend.py tests/test_api_backend.py
git commit -m "feat: the real Google calls, with etag concurrency on the event patch path"
```

---

### Task 8: Calendar — and responding to an invitation

Review Focus #2 lives here. There is no accept endpoint; this task is why.

**Files:**
- Create: `src/csa_google_gmail_calendar/calendar.py`
- Test: `tests/test_calendar_respond.py`

**Interfaces:**
- Consumes: `backend.Backend`
- Produces: `calendar.Calendar(backend)` with `.respond(calendar_id, event_id, response, comment=None) -> dict`, `.create(...)`, `.reschedule(...)`, `.find_free(...)`; `calendar.RESPONSES = ("accepted", "declined", "tentative")`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_respond.py
import pytest
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar.calendar import Calendar
from csa_google_gmail_calendar.exceptions import NotFoundError, UnsupportedOperation

def _ev(attendees, etag='"v1"'):
    return {"id": "e1", "etag": etag, "summary": "Standup",
            "start": {"dateTime": "2026-10-01T09:00:00Z"}, "attendees": attendees}

def test_accepting_sets_only_my_response_status():
    ev = _ev([{"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
              {"email": "other@example.org", "responseStatus": "accepted"}])
    fake = FakeBackend(events={"e1": ev})
    Calendar(fake).respond("primary", "e1", "accepted")
    out = fake.events["e1"]["attendees"]
    assert out[0]["responseStatus"] == "accepted"
    assert out[1]["responseStatus"] == "accepted", "other attendees must be untouched"

def test_declining_does_not_clobber_another_attendees_response():
    ev = _ev([{"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
              {"email": "other@example.org", "responseStatus": "tentative"}])
    fake = FakeBackend(events={"e1": ev})
    Calendar(fake).respond("primary", "e1", "declined")
    assert fake.events["e1"]["attendees"][1]["responseStatus"] == "tentative"

def test_no_self_attendee_refuses_and_says_which_case():
    """REVIEW FOCUS #2. `self` is read-only and marks which attendee is you. Without it,
    patching attendee zero would answer on a stranger's behalf."""
    fake = FakeBackend(events={"e1": _ev([{"email": "a@example.org",
                                           "responseStatus": "needsAction"}])})
    with pytest.raises(UnsupportedOperation, match="not among the .* attendee"):
        Calendar(fake).respond("primary", "e1", "accepted")

def test_an_event_with_no_attendees_says_it_is_not_an_invitation():
    """REVIEW FOCUS #2, the other half. An event you created for yourself has no attendees
    at all — a different problem from being uninvited, and a different remedy."""
    fake = FakeBackend(events={"e1": {"id": "e1", "etag": '"v1"', "summary": "Focus time"}})
    with pytest.raises(UnsupportedOperation, match="no attendees"):
        Calendar(fake).respond("primary", "e1", "accepted")

def test_an_unknown_response_value_is_refused_with_the_valid_set():
    fake = FakeBackend(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                           "responseStatus": "needsAction"}])})
    with pytest.raises(ValueError, match="accepted"):
        Calendar(fake).respond("primary", "e1", "maybe")

def test_the_etag_from_the_read_is_sent_on_the_write():
    """The event is read, modified and written back. Without If-Match, a change between
    those two calls is silently overwritten."""
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw); return super().update_event(**kw)
    fake = Spy(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                   "responseStatus": "needsAction"}], etag='"v7"')})
    Calendar(fake).respond("primary", "e1", "accepted")
    assert seen["etag"] == '"v7"'

def test_a_comment_becomes_the_attendee_comment():
    fake = FakeBackend(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                           "responseStatus": "needsAction"}])})
    Calendar(fake).respond("primary", "e1", "tentative", comment="might be travelling")
    assert fake.events["e1"]["attendees"][0]["comment"] == "might be travelling"

def test_a_missing_event_raises_notfound():
    with pytest.raises(NotFoundError):
        Calendar(FakeBackend()).respond("primary", "nope", "accepted")

def test_only_the_attendees_field_is_sent_back():
    """Patching the whole event would resend summary, start and end — turning a response
    into an edit, and racing anyone who moved the meeting."""
    seen = {}
    class Spy(FakeBackend):
        def update_event(self, **kw):
            seen.update(kw); return super().update_event(**kw)
    fake = Spy(events={"e1": _ev([{"email": "me@example.com", "self": True,
                                   "responseStatus": "needsAction"}])})
    Calendar(fake).respond("primary", "e1", "accepted")
    assert set(seen["body"]) == {"attendees"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calendar_respond.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar.calendar'`

- [ ] **Step 3: Write the implementation**

```python
# src/csa_google_gmail_calendar/calendar.py
"""Calendar operations, and the one that does not exist as an endpoint.

**There is no accept.** Responding to an invitation is `events.patch` setting your own
attendee's `responseStatus`, and `attendee.self` — the flag saying which one is you — is
READ-ONLY. So the server must read the event, find itself in the attendee list, and write the
list back. Three consequences the tests pin:

* attendees is a full-replace field, so the whole list goes back or the others are dropped;
* only `attendees` goes in the patch body — sending summary/start/end as well turns a response
  into an edit and races whoever moved the meeting;
* the etag from the read is sent as `If-Match`, so a change between the read and the write is
  a refusal rather than a silent overwrite.
"""
from __future__ import annotations

from typing import Any

from .exceptions import UnsupportedOperation

RESPONSES = ("accepted", "declined", "tentative")


class Calendar:
    def __init__(self, backend: Any) -> None:
        self._b = backend

    def respond(self, calendar_id: str, event_id: str, response: str,
                comment: str | None = None) -> dict[str, Any]:
        if response not in RESPONSES:
            raise ValueError(
                f"{response!r} is not a response. Valid values: {', '.join(RESPONSES)}.")
        event = self._b.get_event(calendar_id=calendar_id, event_id=event_id)
        attendees = list(event.get("attendees") or ())
        if not attendees:
            raise UnsupportedOperation(
                f"event {event_id!r} has no attendees, so there is nothing to respond to. An "
                f"event with no attendee list is not an invitation - it is an entry on a "
                f"calendar. To remove it, delete the event instead.")
        mine = [a for a in attendees if a.get("self")]
        if not mine:
            raise UnsupportedOperation(
                f"you are not among the {len(attendees)} attendee(s) on event {event_id!r}, so "
                f"there is no response of yours to set. Responding on another attendee's "
                f"behalf is not something this server will do.")
        # `self` is read-only but Google ignores it on the way back in, and keeping the
        # attendee dicts whole is what preserves everyone else's responseStatus.
        for attendee in mine:
            attendee["responseStatus"] = response
            if comment is not None:
                attendee["comment"] = comment
        return self._b.update_event(
            calendar_id=calendar_id, event_id=event_id,
            body={"attendees": attendees},          # attendees ONLY - see the module docstring
            etag=event.get("etag"),
            # Responding is not an edit to the meeting; nobody needs mail about it.
            send_updates="none")
```

Also `create(...)` (validating that `start` and `end` are both present and that `end` is not
before `start`), `reschedule(...)` (patch of `start`/`end` only, with etag), and `find_free(...)`
over `query_freebusy`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calendar_respond.py -v` → 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/calendar.py tests/test_calendar_respond.py
git commit -m "feat: responding to an invitation, which is a patch of yourself in a list you must find"
```

---

### Task 9: The OAuth flow

Review Focus #4 lives here.

**Files:**
- Create: `src/csa_google_gmail_calendar/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `scopes`, `policy`, `exceptions.AuthError`
- Produces: `auth.scopes_for(enabled: frozenset[str]) -> list[str]`; `auth.needs_reconsent(granted, required) -> bool`; `auth.ScopesMissingError(AuthError)` with `.scopes`; `auth.load_cached_credentials(token_path, required)`; `auth.load_credentials(client_secrets, token_path, required)`; `auth.token_path_default() -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
import json, pytest
from csa_google_gmail_calendar import auth, policy
from csa_google_gmail_calendar.exceptions import AuthError

B = "https://www.googleapis.com/auth/"

def test_read_only_capabilities_request_read_only_scopes():
    got = set(auth.scopes_for(frozenset({policy.MAIL_READ, policy.CALENDAR_READ})))
    assert f"{B}gmail.readonly" in got and f"{B}calendar.events.readonly" in got
    assert f"{B}gmail.modify" not in got, "spec §4: request what the capabilities need, no more"

def test_enabling_send_adds_exactly_the_send_scope():
    base = set(auth.scopes_for(frozenset({policy.MAIL_READ})))
    with_send = set(auth.scopes_for(frozenset({policy.MAIL_READ, policy.MAIL_SEND})))
    assert with_send - base == {f"{B}gmail.send"}

def test_mail_write_needs_modify_and_says_so_only_when_enabled():
    assert f"{B}gmail.modify" in auth.scopes_for(frozenset({policy.MAIL_WRITE}))
    assert f"{B}gmail.modify" not in auth.scopes_for(frozenset({policy.MAIL_READ}))

def test_full_mailbox_scope_is_never_requested_without_mail_delete():
    every_but_delete = frozenset(policy.ALL_CAPABILITIES) - {policy.MAIL_DELETE}
    assert "https://mail.google.com/" not in auth.scopes_for(every_but_delete)

def test_a_granted_write_scope_satisfies_a_required_read_scope():
    assert not auth.needs_reconsent([f"{B}gmail.modify"], [f"{B}gmail.readonly"])

def test_a_missing_scope_needs_reconsent():
    assert auth.needs_reconsent([f"{B}gmail.readonly"], [f"{B}gmail.send"])

def test_scope_short_token_raises_its_own_type_not_a_generic_auth_error(tmp_path):
    """REVIEW FOCUS #4. 'Your login is fine but one scope short' is a different instruction
    from 'you have not logged in', and the remedy reads differently even though the command
    is the same."""
    tok = tmp_path / "token.json"
    tok.write_text(json.dumps({"token": "x", "refresh_token": "y", "client_id": "c",
                               "client_secret": "s", "scopes": [f"{B}gmail.readonly"]}))
    with pytest.raises(auth.ScopesMissingError) as ei:
        auth.load_cached_credentials(str(tok), [f"{B}gmail.readonly", f"{B}gmail.send"])
    assert ei.value.scopes == [f"{B}gmail.send"]
    assert "re-consent" in str(ei.value)

def test_an_absent_token_is_a_plain_auth_error_not_a_scope_error(tmp_path):
    with pytest.raises(AuthError) as ei:
        auth.load_cached_credentials(str(tmp_path / "nope.json"), [f"{B}gmail.readonly"])
    assert not isinstance(ei.value, auth.ScopesMissingError)

def test_a_corrupt_token_file_does_not_echo_its_contents(tmp_path):
    """Never interpolate the cause — it may carry token material."""
    tok = tmp_path / "token.json"; tok.write_text("{not json")
    with pytest.raises(AuthError) as ei:
        auth.load_cached_credentials(str(tok), [f"{B}gmail.readonly"])
    assert "not json" not in str(ei.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar.auth'`

- [ ] **Step 3: Port the flow**

Copy `../csa-google-workspace/src/csa_google_workspace/auth.py` and adapt:
- `scopes_for(read_only)` becomes `scopes_for(enabled: frozenset[str])`, driven by a
  `_CAPABILITY_SCOPES: dict[str, tuple[str, ...]]` map keyed on the capability constants.
- Keep `ScopesMissingError`, `needs_reconsent`, `_read_cached`, `_harden` (the `icacls`
  Windows ACL tightening) and the "do not interpolate the cause" rule **verbatim**.
- `has_write_scope` is dropped: it existed for `CSA_GW_READ_ONLY`, which this server replaces
  with the capability set.

```python
_CAPABILITY_SCOPES: dict[str, tuple[str, ...]] = {
    policy.MAIL_READ:       (f"{_BASE}gmail.readonly",),
    policy.MAIL_WRITE:      (f"{_BASE}gmail.modify",),
    policy.MAIL_SEND:       (f"{_BASE}gmail.send",),
    policy.MAIL_DELETE:     ("https://mail.google.com/",),
    policy.CALENDAR_READ:   (f"{_BASE}calendar.events.readonly",
                             f"{_BASE}calendar.calendarlist.readonly",
                             f"{_BASE}calendar.freebusy"),
    policy.CALENDAR_WRITE:  (f"{_BASE}calendar.events",),
    policy.CALENDAR_DELETE: (f"{_BASE}calendar.events",),
}


def scopes_for(enabled: frozenset[str]) -> list[str]:
    """Spec §4: the scopes the ENABLED capabilities need, nothing more.

    Both official servers over-declare — Gmail advertises the full-mailbox scope while
    shipping no permanent-delete tool. A client granting the declared set grants more than
    the tools can exercise, and this is the function that stops us doing the same.
    """
    wanted: set[str] = set()
    for capability in sorted(enabled):
        wanted.update(_CAPABILITY_SCOPES.get(capability, ()))
    return sorted(wanted)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v` → 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/auth.py tests/test_auth.py
git commit -m "feat: OAuth, with the scope set derived from the capabilities rather than declared flat"
```

---

### Task 10: MCP server skeleton, error ladder, and the auth tools

**Files:**
- Create: `src/csa_google_gmail_calendar/mcp/__init__.py`, `__main__.py`, `cli.py`, `server.py`,
  `_logging.py`, `_untrusted.py`, `_config.py`, `_capabilities.py`,
  `_tools/__init__.py`, `_tools/_base.py`, `_tools/auth.py`, `_tools/config.py`
- Port: `_auth_flow.py`, `_login.py`, `_desktop.py`, `_success_page.py` from the sibling
- Test: `tests/test_mcp_server_shape.py`, `tests/test_mcp_capabilities.py`

**Interfaces:**
- Consumes: everything above
- Produces: `create_server(...) -> MCPServer`; `_base.{READ, WRITE, DESTRUCTIVE, _errors}`; `_capabilities.TOOL_CAPABILITIES`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_capabilities.py
import pytest
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar.mcp._capabilities import TOOL_CAPABILITIES
from csa_google_gmail_calendar import policy

def _tool_names(server):
    return {t.name for t in server._tool_manager.list_tools()}

def test_every_registered_tool_is_declared():
    """Fail-closed. A tool that arrives undeclared silently widens what the server claims."""
    server = create_server(backend=None, policy=policy.Policy())
    undeclared = _tool_names(server) - set(TOOL_CAPABILITIES)
    assert not undeclared, f"undeclared tools: {sorted(undeclared)}"

def test_every_declaration_corresponds_to_a_real_tool():
    """The reverse. A declaration with no tool tells a model a capability is reachable
    when it is not — the bug csa-google-workspace found by having a model read it."""
    server = create_server(backend=None, policy=policy.Policy())
    phantom = set(TOOL_CAPABILITIES) - _tool_names(server)
    assert not phantom, f"declared but not registered: {sorted(phantom)}"

def test_declared_capability_matches_the_backend_gate():
    """The F1 inconsistency in the sibling: a hand-written map disagreed with the gate and
    then propagated into tool descriptions and the demonstration plan."""
    name_to_method = {
        "send_message": "send_message", "trash_email": "trash_message",
        "archive_email": "archive_message", "respond_to_event": "respond_to_event",
        "delete_event": "delete_event", "create_draft": "create_draft",
    }
    for tool, method in name_to_method.items():
        assert TOOL_CAPABILITIES[tool] == policy._GATES[method].capability, tool

def test_no_tool_is_named_delete_email():
    """ADR-001: a tool name is a claim made to a reader who cannot check it."""
    server = create_server(backend=None, policy=policy.Policy())
    assert "delete_email" not in _tool_names(server)

def test_disabled_capabilities_hide_their_tools_rather_than_refusing():
    """A tool that exists and refuses still spends the model's attention."""
    reads_only = policy.Policy(frozenset({policy.MAIL_READ, policy.CALENDAR_READ}))
    names = _tool_names(create_server(backend=None, policy=reads_only))
    assert "send_message" not in names and "search_messages" in names

def test_every_tool_declares_open_world_hint():
    server = create_server(backend=None, policy=policy.Policy())
    for tool in server._tool_manager.list_tools():
        assert tool.annotations.open_world_hint is True, tool.name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_capabilities.py -v`
Expected: FAIL — `No module named 'csa_google_gmail_calendar.mcp'`

- [ ] **Step 3: Write the skeleton**

`_base.py` ports the sibling's `_errors` decorator verbatim with the exception ladder adapted
to this package's `exceptions`, keeping both invariants its docstring records: **raise the SDK's
`ToolError`** (anything else is suppressed), and **never log arguments** (mail bodies are
content; stderr is persisted by the client into a cache we cannot purge).

`create_server(backend, policy, flavour="full", attach_policy=None)` registers only tools whose
declared capability is in `policy.enabled` — a **registration-time** filter, not a refusal.

`_tools/auth.py` provides `authenticate`, `auth_status` (three-state: no token / token present
but scope-short / ready, with no network call) and `logout`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_capabilities.py tests/test_mcp_server_shape.py -v` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/mcp/ tests/test_mcp_capabilities.py tests/test_mcp_server_shape.py
git commit -m "feat: the server, where a disabled capability means an absent tool rather than a refusing one"
```

---

### Task 11: The Gmail tools

**Files:**
- Create: `src/csa_google_gmail_calendar/mcp/_tools/mail_read.py`, `mail_write.py`, `mail_send.py`, `_schemas.py`
- Test: `tests/test_mail_tools.py`

**Interfaces:**
- Produces the 23 Gmail tools from spec §5's minimum set, less `list_history`/`get_profile`
  (Task 13) — registered via `register_mail_read_tools`, `register_mail_write_tools`,
  `register_mail_send_tools`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mail_tools.py
import pytest
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar import policy

def _call(server, name, **kw):
    return server._tool_manager.get_tool(name).fn(**kw)

def test_send_message_puts_exactly_one_message_on_the_wire():
    fake = FakeBackend()
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "send_message", to=["a@example.com"], subject="Hi", body="There")
    assert len(fake.sent) == 1

def test_reply_all_keeps_every_original_recipient_except_me():
    """The whole reason reply_all is its own tool: getting this wrong is public."""
    fake = FakeBackend(messages={"m1": {
        "id": "m1", "threadId": "t1", "payload": {"headers": [
            {"name": "From", "value": "sender@example.org"},
            {"name": "To", "value": "me@example.com, other@example.org"},
            {"name": "Cc", "value": "cc@example.net"},
            {"name": "Message-ID", "value": "<orig@example.org>"}]}}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "reply_all", message_id="m1", body="ack")
    raw = fake.sent[-1]
    assert "sender@example.org" in raw and "other@example.org" in raw
    assert "cc@example.net" in raw
    assert raw.count("me@example.com") == 0, "never reply to yourself"

def test_reply_sets_in_reply_to_from_the_original_message_id():
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": [
        {"name": "From", "value": "s@example.org"},
        {"name": "Message-ID", "value": "<orig@example.org>"}]}}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "reply", message_id="m1", body="ack")
    assert "<orig@example.org>" in fake.sent[-1]

def test_reply_subject_gets_one_re_prefix_not_two():
    fake = FakeBackend(messages={"m1": {"id": "m1", "threadId": "t1", "payload": {"headers": [
        {"name": "From", "value": "s@example.org"},
        {"name": "Subject", "value": "Re: already a reply"}]}}},
        profile={"emailAddress": "me@example.com"})
    s = create_server(backend=fake, policy=policy.Policy())
    _call(s, "reply", message_id="m1", body="ack")
    assert fake.sent[-1].count("Re:") == 1

def test_an_unknown_argument_is_refused_rather_than_ignored():
    """csa-zendesk's lesson: a silently-dropped argument is a silently-wrong call."""
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    with pytest.raises(Exception, match="unknown argument"):
        _call(s, "send_message", to=["a@example.com"], subject="s", body="b", bbc=["x@y.z"])

def test_attachments_without_a_configured_dir_refuse_with_the_variable_name():
    s = create_server(backend=FakeBackend(), policy=policy.Policy(), attach_policy=None)
    with pytest.raises(Exception, match="CSA_GGC_ATTACH_DIR"):
        _call(s, "send_message", to=["a@example.com"], subject="s", body="b",
              attachments=["anything.pdf"])

def test_get_attachment_writes_to_the_attachment_dir_and_returns_the_path(tmp_path):
    from csa_google_gmail_calendar._attachments import AttachmentPolicy
    d = tmp_path / "a"; d.mkdir()
    fake = FakeBackend(attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(),
                      attach_policy=AttachmentPolicy(str(d)))
    out = _call(s, "get_attachment", message_id="m1", attachment_id="att-1",
                filename="note.txt")
    assert (d / "note.txt").read_bytes() == b"hello"
    assert "note.txt" in str(out)

def test_a_downloaded_attachment_filename_cannot_escape_the_dir(tmp_path):
    """The filename comes from the MESSAGE, which a stranger wrote."""
    from csa_google_gmail_calendar._attachments import AttachmentPolicy
    d = tmp_path / "a"; d.mkdir()
    fake = FakeBackend(attachments={"att-1": {"data": "aGVsbG8", "size": 5}})
    s = create_server(backend=fake, policy=policy.Policy(),
                      attach_policy=AttachmentPolicy(str(d)))
    with pytest.raises(Exception, match="outside|invalid"):
        _call(s, "get_attachment", message_id="m1", attachment_id="att-1",
              filename="../../escaped.txt")

def test_trash_email_exists_and_delete_email_does_not():
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    names = {t.name for t in s._tool_manager.list_tools()}
    assert "trash_email" in names and "untrash_email" in names
    assert "delete_email" not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mail_tools.py -v`
Expected: FAIL — tools not registered

- [ ] **Step 3: Write the tool modules**

Each tool's docstring is its model-facing description and follows the sibling's six guidance
patterns (cross-tool routing, prefer/instead, negative capability, empty-result disambiguation,
context-cost steering, upstream-behaviour warnings). `reply_all` computes recipients as
`{From} ∪ {To} ∪ {Cc} − {my own address from get_profile}`, deduplicated case-insensitively.
`get_attachment` writes into the attachment directory and runs the **message-supplied filename**
through `AttachmentPolicy.resolve`'s containment check before writing.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mail_tools.py -v` → 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/mcp/_tools/ tests/test_mail_tools.py
git commit -m "feat: the mail tools, and a reply_all that never answers to itself"
```

---

### Task 12: The Calendar tools

**Files:**
- Create: `src/csa_google_gmail_calendar/mcp/_tools/calendar_read.py`, `calendar_write.py`
- Test: `tests/test_calendar_tools.py`

**Interfaces:**
- Produces: `list_calendars`, `list_events`, `get_event`, `find_free_time`, `create_event`, `update_event`, `respond_to_event`, `delete_event`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_tools.py
import pytest
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar.backend import FakeBackend
from csa_google_gmail_calendar import policy

def _call(server, name, **kw):
    return server._tool_manager.get_tool(name).fn(**kw)

def test_respond_to_event_is_one_tool_with_a_constrained_response_argument():
    """ADR-016: a tool is (operation x constrained arguments). Three tools would be three
    descriptions saying the same thing."""
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    names = {t.name for t in s._tool_manager.list_tools()}
    assert "respond_to_event" in names
    assert not {"accept_event", "decline_event"} & names
    schema = s._tool_manager.get_tool("respond_to_event").input_schema
    assert set(schema["properties"]["response"]["enum"]) == {"accepted", "declined", "tentative"}

def test_create_event_refuses_an_end_before_its_start():
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    with pytest.raises(Exception, match="before"):
        _call(s, "create_event", summary="Backwards",
              start="2026-10-01T10:00:00Z", end="2026-10-01T09:00:00Z")

def test_create_event_defaults_to_notifying_attendees():
    """An invitation nobody is told about is not an invitation."""
    seen = {}
    class Spy(FakeBackend):
        def create_event(self, **kw): seen.update(kw); return super().create_event(**kw)
    s = create_server(backend=Spy(), policy=policy.Policy())
    _call(s, "create_event", summary="Sync", start="2026-10-01T09:00:00Z",
          end="2026-10-01T10:00:00Z", attendees=["a@example.com"])
    assert seen["send_updates"] == "all"

def test_delete_event_is_absent_when_calendar_delete_is_off():
    s = create_server(backend=FakeBackend(), policy=policy.Policy())
    assert "delete_event" not in {t.name for t in s._tool_manager.list_tools()}

def test_delete_event_appears_when_the_capability_is_enabled():
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    s = create_server(backend=FakeBackend(), policy=p)
    assert "delete_event" in {t.name for t in s._tool_manager.list_tools()}

def test_delete_event_is_annotated_destructive():
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    s = create_server(backend=FakeBackend(), policy=p)
    assert s._tool_manager.get_tool("delete_event").annotations.destructive_hint is True

def test_find_free_time_returns_gaps_not_busy_blocks():
    """A model asked 'when are we free' should not have to invert the answer itself."""
    fake = FakeBackend(freebusy={"primary": [
        {"start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"}]})
    s = create_server(backend=fake, policy=policy.Policy())
    out = _call(s, "find_free_time", time_min="2026-10-01T08:00:00Z",
                time_max="2026-10-01T12:00:00Z", calendar_ids=["primary"])
    assert any(g["start"] == "2026-10-01T10:00:00Z" for g in out["free"])
    assert any(g["end"] == "2026-10-01T09:00:00Z" for g in out["free"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calendar_tools.py -v`
Expected: FAIL — tools not registered

- [ ] **Step 3: Write the tool modules**

`find_free_time` inverts the busy blocks from `query_freebusy` into gaps — the question people
ask is "when are we free", and returning busy intervals makes the model do arithmetic it gets
wrong. `respond_to_event` carries a `response` enum of exactly the three values.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calendar_tools.py -v` → 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/csa_google_gmail_calendar/mcp/_tools/calendar_read.py \
        src/csa_google_gmail_calendar/mcp/_tools/calendar_write.py tests/test_calendar_tools.py
git commit -m "feat: calendar tools, answering when you are free rather than when you are busy"
```

---

### Task 13: Configuration surface, the demo, CI and docs

**Files:**
- Create: `src/csa_google_gmail_calendar/mcp/_tools/demo.py`, `feedback.py`, `_flavours.py`, `_resources.py`
- Create: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `CHANGELOG.md`, `RELEASING.md`
- Modify: `README.md`, `TODO.md`, `CLAUDE.md`
- Test: `tests/test_config_tools.py`, `tests/test_demo.py`, `tests/test_docs_drift.py`

**Interfaces:**
- Produces: `describe_configuration`, `demonstration_plan`, `report_a_problem`, `list_history`, `get_profile`, `whoami`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docs_drift.py
import pathlib, re
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar import policy

def test_readme_lists_every_tool_the_server_registers():
    """Documentation drift as a testable property (TESTING.md). A README tool table nobody
    checks is a promise that decays silently."""
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    registered = {t.name for t in create_server(backend=None, policy=p)._tool_manager.list_tools()}
    readme = pathlib.Path("README.md").read_text()
    documented = set(re.findall(r"`([a-z_]+)`", readme))
    assert not registered - documented, f"undocumented: {sorted(registered - documented)}"

def test_every_env_var_the_code_reads_is_in_the_readme():
    src = pathlib.Path("src").rglob("*.py")
    used = set()
    for f in src:
        used |= set(re.findall(r'"(CSA_GGC_[A-Z_]+)"', f.read_text()))
    readme = pathlib.Path("README.md").read_text()
    assert not {v for v in used if v not in readme}
```

```python
# tests/test_demo.py
def test_the_demo_plan_names_only_tools_that_exist():
    from csa_google_gmail_calendar.mcp import create_server
    from csa_google_gmail_calendar import policy
    s = create_server(backend=None, policy=policy.Policy())
    names = {t.name for t in s._tool_manager.list_tools()}
    plan = s._tool_manager.get_tool("demonstration_plan").fn()
    for step in plan["steps"]:
        assert step["tool"] in names, step["tool"]

def test_the_demo_does_not_send_mail_to_anyone_but_the_authenticated_user():
    """A demo that emails a stranger is an incident, not a test."""
    from csa_google_gmail_calendar.mcp import create_server
    from csa_google_gmail_calendar import policy
    s = create_server(backend=None, policy=policy.Policy())
    plan = s._tool_manager.get_tool("demonstration_plan").fn()
    for step in plan["steps"]:
        if step["tool"] in ("send_message", "reply", "reply_all"):
            assert step["arguments"]["to"] == ["<the authenticated user>"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_docs_drift.py tests/test_demo.py -v`
Expected: FAIL — tools and README table absent

- [ ] **Step 3: Write the config surface**

`describe_configuration` reports enabled capabilities, granted scopes, the flavour, whether
`CSA_GGC_ATTACH_DIR` is set (the **path**, not its contents), and **what it is hiding** — a
flavour says what it excludes, per spec §5.

`CSA_GGC_FLAVOUR=google|full|core`: `core` is the 31-tool minimum set from spec §5.

- [ ] **Step 4: Write the demo**

`demonstration_plan` returns a sequence exercising every registered tool, writing only to the
authenticated user's own mailbox and a scratch calendar event it then cleans up. It sends mail
**to the authenticated user only** — never an address from an argument.

- [ ] **Step 5: Write CI**

`.github/workflows/ci.yml` on the 3.10–3.14 matrix: `ruff check`, `mypy`, `pytest --cov
--cov-fail-under=90`. All actions SHA-pinned per `PUBLIC-GITHUB-REPO-STANDARDS.md`.
`release.yml` uses Trusted Publishing with attestations and holds no project code.

- [ ] **Step 6: Write the README and update the indices**

README documents every tool, every `CSA_GGC_*` variable, the capability table, and the three
structural absences (no move, no attachment upload, no receive). `TODO.md` gets a line per
open item; `CLAUDE.md` gets the repo's working rules.

- [ ] **Step 7: Run the full suite**

Run: `ruff check . && mypy && pytest --cov --cov-fail-under=90 -q`
Expected: all pass, coverage ≥90%

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: the configuration surface, a demo that writes only to your own mailbox, and CI"
```

---

### Task 14: Live probe against a real account

Not runnable in CI — needs a real Google account. Every prior task's verdict is offline, and
csa-zendesk's record is that live testing found what offline could not on every single run
(F1: 4 findings, F2: 8, H1/H2 inverted two verdicts).

**Files:**
- Create: `experiments/2026-09-25-first-live-probe/RESULTS.md`
- Create: `tests/integration/test_live_gmail.py`, `tests/integration/test_live_calendar.py` (marked `@pytest.mark.integration`, skipped without `CSA_GGC_LIVE=1`)

- [ ] **Step 1: Run the OAuth flow end to end**

Confirm the browser opens, consent lists **only** the scopes the enabled capabilities need,
and the token lands at the expected path with 0600 permissions.

- [ ] **Step 2: Read a real HTML-only message**

Confirm the body is converted rather than empty — Review Focus #3 against real mail rather
than a fixture.

- [ ] **Step 3: Send a message with an attachment to yourself**

Confirm it arrives, the attachment opens, and the filename survives.

- [ ] **Step 4: Probe the allowlist with a real escape**

Attempt `attachments=["../../.ssh/id_rsa"]` and a symlink planted inside the attachment dir.
Both must refuse. **Use a real file that exists** — csa-zendesk's lesson was that probing a
control with a non-existent target cannot distinguish the control firing from a 404.

- [ ] **Step 5: Respond to a real invitation**

Have a second account invite the first; accept it; confirm the organiser sees the acceptance
and that no other attendee's status changed.

- [ ] **Step 6: Record every finding in RESULTS.md and file an issue per finding**

- [ ] **Step 7: Commit**

```bash
git add experiments/ tests/integration/
git commit -m "test: the first live probe, and what only a real account could show"
```
