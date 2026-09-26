"""`mcp._untrusted` in isolation - terminal-control neutralisation, and (FIX 3, final
whole-branch review, CINO 2026-09-26) the Trojan-Source bidi-override/BOM defusal `scrub` used
to leave to `_markdown.strip_suspicious` alone, which only ever runs on a message BODY. A
subject, a `From` display name, an `AttachmentRef.filename`, an event summary, or an attendee
name never goes through `_markdown` at all - `scrub` is the only seam all of them share.
"""
from csa_google_gmail_calendar.mcp import _untrusted

BIDI_RLO = "\u202e"
BIDI_LRO = "\u202d"
BOM = "\ufeff"


def test_neutralise_still_defuses_c0_and_del():
    assert "\x1b" not in _untrusted.neutralise("hi\x1b[2Kbye")
    assert "␛" in _untrusted.neutralise("hi\x1b[2Kbye")


def test_neutralise_defuses_a_trojan_source_bidi_override_in_a_filename():
    """The canonical Trojan Source example: an RLO reorders `gnp.exe` to display as `exe.png`
    reversed, i.e. the attacker names a file so it reads as an innocuous extension."""
    evil_filename = "invoice" + BIDI_RLO + "fdp.exe"
    cleaned = _untrusted.neutralise(evil_filename)
    assert BIDI_RLO not in cleaned
    assert "�" in cleaned


def test_neutralise_defuses_a_bidi_override_in_a_subject():
    evil_subject = "Re: Invoice" + BIDI_LRO + " urgent" + BIDI_RLO
    cleaned = _untrusted.neutralise(evil_subject)
    assert BIDI_LRO not in cleaned and BIDI_RLO not in cleaned


def test_neutralise_defuses_a_bidi_override_in_an_event_summary():
    evil_summary = "Team sync" + BIDI_RLO + "gnp.kcatta"
    cleaned = _untrusted.neutralise(evil_summary)
    assert BIDI_RLO not in cleaned


def test_neutralise_defuses_a_mid_document_bom():
    cleaned = _untrusted.neutralise("hello" + BOM + "world")
    assert BOM not in cleaned


def test_neutralise_keeps_tab_newline_and_bare_cr():
    text = "line one\nline two\ttabbed\rline three"
    cleaned = _untrusted.neutralise(text)
    assert cleaned == text


def test_scrub_walks_nested_structures_and_defuses_bidi_everywhere():
    """Filename, subject and event summary, all in one result - the three surfaces FIX 3 names
    explicitly, walked the same way a real tool result would nest them."""
    result = {
        "attachments": [{"filename": "invoice" + BIDI_RLO + "fdp.exe"}],
        "subject": "Re:" + BIDI_LRO + "urgent",
        "event": {"summary": "sync" + BIDI_RLO + "note", "attendees": ["A" + BOM + "licia"]},
    }
    scrubbed = _untrusted.scrub(result)
    assert BIDI_RLO not in scrubbed["attachments"][0]["filename"]
    assert BIDI_LRO not in scrubbed["subject"]
    assert BIDI_RLO not in scrubbed["event"]["summary"]
    assert BOM not in scrubbed["event"]["attendees"][0]


def test_scrub_also_defuses_a_suspicious_dict_key():
    scrubbed = _untrusted.scrub({("lab" + BIDI_RLO + "el"): "value"})
    assert list(scrubbed)[0] != "lab" + BIDI_RLO + "el"
    assert BIDI_RLO not in list(scrubbed)[0]


def test_suspicious_count_is_zero_for_clean_content():
    assert _untrusted.suspicious_count({"subject": "ordinary subject", "n": 3, "ok": None}) == 0


def test_suspicious_count_counts_every_defused_codepoint_across_the_structure():
    result = {"filename": "a" + BIDI_RLO + "b", "subject": "c" + BOM + "d" + "\x1b"}
    # 1 (RLO in filename) + 1 (BOM) + 1 (ESC) = 3.
    assert _untrusted.suspicious_count(result) == 3


def test_suspicious_count_matches_what_scrub_actually_changed():
    text = "clean " + BIDI_RLO + " text"
    count = _untrusted.suspicious_count(text)
    scrubbed = _untrusted.scrub(text)
    assert count == 1
    assert scrubbed != text


def test_no_live_bidi_control_character_exists_anywhere_in_this_repository() -> None:
    """Source must NAME a bidi override, never contain one.

    Bandit caught this the honest way: `mcp/_untrusted.py`'s own docstring — the prose
    explaining why bidi overrides are stripped from message bodies — contained a live
    U+202E. The file defending against Trojan Source was itself a Trojan Source sample,
    and every reader of it on GitHub saw reordered text.

    A test fixture needing the real character writes `"\\u202e"`: identical value, readable
    source. Prose names the codepoint and carries none. This test is what keeps it that way,
    because the failure mode is invisible in every editor that renders the file correctly.
    """
    import pathlib
    import subprocess

    dangerous = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0xFEFF}
    root = pathlib.Path(__file__).resolve().parent.parent
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.md"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.split()
    offenders = []
    for name in tracked:
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            found = sorted({f"U+{ord(c):04X}" for c in line if ord(c) in dangerous})
            if found:
                offenders.append(f"{name}:{number} {','.join(found)}")
    assert not offenders, (
        "live bidi/BOM control characters in tracked source — write the escape "
        "(\\u202e) where a value is needed, or name the codepoint in prose:\n  "
        + "\n  ".join(offenders))
