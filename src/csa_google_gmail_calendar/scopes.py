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

`narrowest()` answers what the API would ACCEPT for one method considered alone, never what
this server should REQUEST across everything a capability turns on — see `auth._CAPABILITY_SCOPES`
and carry-forward-task-9.md for the two cases (`events.get`, `events.insert`) where the narrowest
accepted scope is unusable for how this server actually calls the method.

RANK is a deliberate totalisation of what is really only a partial order. A Discovery method's
`scopes` list is OR-alternatives — any one of them suffices — so all `narrowest()` ever needs is
"which of these candidates is least", never a global comparison between two arbitrary scopes.
Some pairs genuinely aren't comparable (`gmail.settings.basic` grants nothing over mailbox
content and nothing is narrower than it in that dimension; `calendar.settings.readonly` reads
account settings, not events, so it isn't "narrower than" event-access scopes either) — RANK
picks a total order anyway because a method needs one answer. A future scope Google adds could
be misranked the same way `calendar.calendars.readonly` was once simply omitted (see the
completeness test in tests/test_scopes.py): omission and misplacement are the same class of risk.
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
    f"{_BASE}calendar.calendars.readonly",  # read-only metadata (title, timezone) for a single
                                             # calendar, as against calendarlist.readonly above,
                                             # which reads the user's subscription list instead
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
