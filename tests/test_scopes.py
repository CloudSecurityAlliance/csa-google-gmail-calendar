import json
import pathlib

import pytest

from csa_google_gmail_calendar import scopes

B = "https://www.googleapis.com/auth/"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS = [ROOT / "specs/gmail-v1-discovery.json", ROOT / "specs/calendar-v3-discovery.json"]

def test_readonly_beats_modify_for_a_gmail_read():
    """The bug in #11: min(scopes, key=len) picked gmail.modify (12) over gmail.readonly (14).

    NOTE: the brief's candidate list also included gmail.metadata, which -- being strictly
    narrower than gmail.readonly per RANK and per test_metadata_is_narrower_than_readonly below
    -- would make narrowest() correctly return metadata, not readonly, contradicting this test's
    assertion. Dropped here as a transcription artifact; the readonly-vs-modify comparison this
    test is named for is unaffected, and the metadata-vs-readonly comparison is covered below.
    """
    got = scopes.narrowest("gmail", [
        "https://mail.google.com/", f"{B}gmail.modify", f"{B}gmail.readonly",
    ])
    assert got == f"{B}gmail.readonly"

def test_bare_calendar_is_the_broadest_not_the_narrowest():
    """Every Calendar row was wrong: bare `calendar` is full access and the shortest string."""
    got = scopes.narrowest("calendar", [
        f"{B}calendar", f"{B}calendar.events", f"{B}calendar.events.readonly", f"{B}calendar.readonly",
    ])
    assert got == f"{B}calendar.events.readonly"

def test_write_method_gets_the_narrowest_write_scope():
    """calendar.events.owned is restricted to calendars the caller owns; calendar.events reaches
    every calendar the caller can access. Fewer calendars is less privilege, so owned is
    narrower -- matching RANK's order and Google's own scope descriptions. (The brief asserted
    calendar.events here, which RANK's own ordering of these three candidates contradicts;
    corrected to match RANK rather than the other way around, per the "don't touch the
    researched ordering" instruction.)
    """
    got = scopes.narrowest("calendar", [f"{B}calendar", f"{B}calendar.events", f"{B}calendar.events.owned"])
    assert got == f"{B}calendar.events.owned"

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

def _walk(resources: dict):
    for _name, node in (resources or {}).items():
        for _method_name, m in (node.get("methods") or {}).items():
            yield from (m.get("scopes") or [])
        yield from _walk(node.get("resources") or {})

def test_every_scope_in_the_discovery_documents_is_ranked_or_excluded_by_name():
    """A scope that is neither in RANK nor in UNRANKED is not "unranked by decision" -- it is
    invisible to narrowest(), which filters candidates with `s in RANK`. That is exactly how
    `calendar.calendars.readonly` went missing: not rejected, just never listed, so every method
    offering it silently fell back to a broader scope -- the same failure shape as #11, this time
    from an omission instead of a heuristic. This test parses the actual Discovery documents so
    the next scope Google adds cannot reintroduce that silently.
    """
    all_scopes: set[str] = set()
    for path in SPECS:
        doc = json.loads(path.read_text())
        all_scopes.update(_walk(doc.get("resources") or {}))

    known = set(scopes.RANK) | scopes.UNRANKED
    missing = all_scopes - known
    assert not missing, (
        f"{len(missing)} scope(s) in the Discovery documents are in neither RANK nor UNRANKED, "
        f"so narrowest() silently drops them as candidates: {sorted(missing)}")
