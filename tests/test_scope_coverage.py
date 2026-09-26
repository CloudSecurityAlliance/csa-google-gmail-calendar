"""Fix round 2, item 3 (Important): derive the "can this method ever be authorised" check
instead of hand-verifying it.

`auth.py`'s module docstring on `_CAPABILITY_SCOPES` used to say the fact that every gated
method's narrowest accepted scope is reachable by its capability's requested scopes "was
verified by hand ... and is not re-derived at runtime" - and hand-verification missed
`get_calendar`: no capability this server has ever requested a scope `calendars.get` accepts,
so it could never be authorised under any configuration, and the hand check said nothing.

This test intersects, for every method `policy._GATES` gates, the set of scopes
`analysis/operation-inventory.csv` lists as ACCEPTED for that method against the set of scopes
its gating capability actually REQUESTS (`auth._CAPABILITY_SCOPES`, the raw per-capability
declaration, not the collapsed `scopes_for` output - collapsing only ever replaces a scope with
one that dominates it, so it cannot turn a non-empty intersection empty, but checking the raw
map is simpler and does not depend on that reasoning holding forever). An empty intersection
means the method is ungrantable and the test fails, naming it - this is what would have caught
`get_calendar` (and, on the Gmail side, `send_draft`) before either was shipped broken.

`_METHOD_TO_DISCOVERY` is a hand-built map from this project's Backend method names to the
Discovery (api, family, method) triple that backs them, read off `backend.py`'s `ApiBackend`
implementation. It is the one part of this test that IS hand-verified - there is no generic way
to derive "which REST method does this Python method call" without executing it - but it is a
mapping of NAMES, not of privilege judgements, so a mistake in it is a `KeyError` (a method
this test cannot find in the CSV) rather than a silently wrong answer.
"""
from __future__ import annotations

import csv
import pathlib

from csa_google_gmail_calendar import auth, policy

ROOT = pathlib.Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "analysis" / "operation-inventory.csv"

# Backend method name -> (api, family, method), read off backend.py's ApiBackend.
_METHOD_TO_DISCOVERY: dict[str, tuple[str, str, str]] = {
    # --- mail reads ---
    "search_messages": ("gmail", "users.messages", "list"),
    "get_message": ("gmail", "users.messages", "get"),
    "get_thread": ("gmail", "users.threads", "get"),
    "list_threads": ("gmail", "users.threads", "list"),
    "get_attachment": ("gmail", "users.messages.attachments", "get"),
    "list_labels": ("gmail", "users.labels", "list"),
    "list_drafts": ("gmail", "users.drafts", "list"),
    "get_draft": ("gmail", "users.drafts", "get"),
    "list_history": ("gmail", "users.history", "list"),
    "get_profile": ("gmail", "users", "getProfile"),
    # --- mail reversible writes ---
    "create_draft": ("gmail", "users.drafts", "create"),
    "update_draft": ("gmail", "users.drafts", "update"),
    "delete_draft": ("gmail", "users.drafts", "delete"),
    "modify_message_labels": ("gmail", "users.messages", "modify"),
    "modify_thread_labels": ("gmail", "users.threads", "modify"),
    # archive/mark_read/mark_unread/mark_spam/unmark_spam all reduce to a messages.modify (or
    # threads.modify) call at the ApiBackend layer - there is no dedicated archive/read/spam
    # REST endpoint, only label mutation (see backend.py's comment on mark_spam).
    "archive_message": ("gmail", "users.messages", "modify"),
    "archive_thread": ("gmail", "users.threads", "modify"),
    "mark_read": ("gmail", "users.messages", "modify"),
    "mark_unread": ("gmail", "users.messages", "modify"),
    "mark_spam": ("gmail", "users.messages", "modify"),
    "unmark_spam": ("gmail", "users.messages", "modify"),
    "trash_message": ("gmail", "users.messages", "trash"),
    "trash_thread": ("gmail", "users.threads", "trash"),
    "untrash_message": ("gmail", "users.messages", "untrash"),
    "untrash_thread": ("gmail", "users.threads", "untrash"),
    "create_label": ("gmail", "users.labels", "create"),
    # --- outbound ---
    "send_message": ("gmail", "users.messages", "send"),
    "send_draft": ("gmail", "users.drafts", "send"),
    # reply/forward are both implemented as messages.send with a different raw MIME body -
    # there is no separate "reply" or "forward" Discovery method.
    "reply_message": ("gmail", "users.messages", "send"),
    "reply_all_message": ("gmail", "users.messages", "send"),
    "forward_message": ("gmail", "users.messages", "send"),
    # --- calendar ---
    "list_calendars": ("calendar", "calendarList", "list"),
    "get_calendar": ("calendar", "calendars", "get"),
    "list_events": ("calendar", "events", "list"),
    "get_event": ("calendar", "events", "get"),
    "query_freebusy": ("calendar", "freebusy", "query"),
    "create_event": ("calendar", "events", "insert"),
    "update_event": ("calendar", "events", "patch"),
    "respond_to_event": ("calendar", "events", "patch"),   # patches an event, same as update
    "delete_event": ("calendar", "events", "delete"),
}


_SCOPE_PREFIX = "https://www.googleapis.com/auth/"


def _full_scope(leaf: str) -> str:
    """`operation-inventory.csv`'s `scopes` column is stripped of `_SCOPE_PREFIX` for display
    (see `scripts/inventory.py`'s own `SCOPE_PREFIX`/`scopes_full` split) - `mail.google.com`
    is the one entry that was never prefixed to begin with, since it isn't a
    `googleapis.com/auth/` scope at all, so it round-trips unchanged."""
    return leaf if leaf.startswith("https://") else f"{_SCOPE_PREFIX}{leaf}"


def _load_accepted_scopes() -> dict[tuple[str, str, str], set[str]]:
    accepted: dict[tuple[str, str, str], set[str]] = {}
    with open(INVENTORY, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["api"], row["family"], row["method"])
            accepted[key] = {_full_scope(s) for s in row["scopes"].split()}
    return accepted


def test_method_to_discovery_map_covers_every_gated_method():
    """Fails loudly (KeyError-shaped, by name) if a Backend method is added to policy._GATES
    without a corresponding entry here, rather than the coverage check below silently skipping
    it."""
    missing = set(policy._GATES) - set(_METHOD_TO_DISCOVERY)
    assert not missing, f"no Discovery mapping for: {sorted(missing)}"


def test_every_gated_method_can_be_authorised_by_its_capability():
    """The derived version of the check `_CAPABILITY_SCOPES`'s module comment says was only
    hand-verified. For every method `policy._GATES` gates, its accepted-scopes column (from
    the Discovery-derived CSV) must intersect the scopes its gating capability requests -
    otherwise the tool ships, is advertised to the model, and 403s forever."""
    accepted = _load_accepted_scopes()
    failures = []
    for method, gate in policy._GATES.items():
        capability = gate.capability
        if capability is None:
            continue  # nothing currently uses Gate(None); see policy.py's fix-round-2 note
        discovery_key = _METHOD_TO_DISCOVERY[method]
        method_scopes = accepted.get(discovery_key)
        assert method_scopes is not None, f"{method} -> {discovery_key} not in {INVENTORY.name}"
        requested = set(auth._CAPABILITY_SCOPES.get(capability, ()))
        if not (method_scopes & requested):
            failures.append(
                f"{method} (gated {capability!r}) accepts {sorted(method_scopes)} but "
                f"{capability!r} requests {sorted(requested)} - no overlap")
    assert not failures, "unauthorisable method(s):\n" + "\n".join(failures)


def test_send_draft_is_specifically_covered_by_the_derived_check():
    """Fix round 2, item 4's own required coverage: send_draft (drafts.send) does not accept
    gmail.send at all, so before MAIL_SEND also requested gmail.compose this method's
    intersection was empty and the test above would have failed naming it."""
    accepted = _load_accepted_scopes()
    method_scopes = accepted[_METHOD_TO_DISCOVERY["send_draft"]]
    assert "https://www.googleapis.com/auth/gmail.send" not in method_scopes
    requested = set(auth._CAPABILITY_SCOPES[policy.MAIL_SEND])
    assert method_scopes & requested, "send_draft must be authorisable by MAIL_SEND's scopes"
