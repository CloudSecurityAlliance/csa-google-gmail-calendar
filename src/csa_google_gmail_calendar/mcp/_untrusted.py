"""Neutralise terminal control sequences in everything a tool returns.

Ported from `../csa-google-workspace/src/csa_google_workspace/mcp/_untrusted.py`. That file's
reasoning is reproduced in full below because it is the expensive part; only the
project-specific field (`capped`/`MAX_REQUEST_MESSAGE`, built for Drive's `request_message`)
is dropped, since nothing in Gmail or Calendar has an analogous short, attacker-authored field
today. If one turns up (a decline note on a calendar response, say) the cap can come back.

## What this defends against, measured rather than assumed

The MCP SDK returns a tool result twice: as `structured_content` (the dict) and as a text block
holding the same dict as pretty-printed JSON. JSON escaping means a value CANNOT forge a sibling
field. What JSON escaping does NOT do is stop the string from carrying **terminal control
sequences**. `json.dumps` writes ESC as `\\u001b`; the client decodes it back to a live `0x1b`
byte before displaying it:

    payload = "please read this\\x1b[2K\\r\\x1b[1A\\x1b[2Kgranted by admin"
    json.loads(json.dumps(payload))     # ESC survives, as a real 0x1b

In a terminal that is *erase line, carriage return, cursor up, erase line* - it deletes the line
it is on **and the line above it**, which is where a warning would have been printed.

**The attack is an asymmetry, and that is what makes it worth fixing.** The model reads
`structured_content`, where the bytes are inert data. The human reads the rendered text, where
they are instructions to the terminal. Every control this project has against prompt injection
assumes the human can see what the model saw - message bodies, event summaries and attendee
names are all written by someone else, and this is the primary risk this project names for
itself.

## Why it is applied at the boundary and not per field

The untrusted strings on this surface are not one field - a message subject, an event summary, an
attendee display name, a label name are all set by somebody else. A hand-maintained list of
fields to sanitise is the shape other CSA MCP servers keep finding broken (csa-google-workspace
#308, #332). So this runs once, in `_tools._base._errors`, which every tool passes through - a
tool added tomorrow is covered without anybody remembering this file exists.

## Why `\\t`, `\\n` and `\\r` are kept

`\\t` and `\\n` are ordinary content: a message body has newlines and would be wrong without
them. `\\r` is kept too - the residual is a bare `\\r` overwriting the line it is on, which
cannot erase a line, move the cursor, or reach the line above (all of those need ESC), and the
value sits inside a quoted JSON string on its own line, so the overwrite is confined to it.

## Trojan-Source (bidi override / BOM), and why it belongs at this same seam (fix round, final
whole-branch review, CINO 2026-09-26)

`_markdown._STRIP_RULES` already strips the Trojan-Source bidi-override pair (U+202D/U+202E)
and a mid-document BOM (U+FEFF) - but only through `mail._render_body`, i.e. only for a
message BODY. Everything else this project reads from someone else and hands back untouched -
a subject line, a `From` display name, `AttachmentRef.filename`, an event summary, an attendee
name - reaches the reader through `scrub`, which (before this fix) neutralised C0/DEL only.
`evil‮gnp.exe` (displays as `evil.png`, Trojan Source's own canonical example) would
therefore survive a filename or a subject unchanged, while the identical bytes in a message
body would already have been stripped - the same content defended in one field and not
another is not a defensible boundary. `_untrusted.py`'s own docstring above already names event
summaries and attendee names as within scope; they were covered against ESC and not against
bidi, which this closes by extending `_CONTROLS`-style defusal (replace, do not silently drop -
see the paragraph above) to the same two Trojan-Source classes `_markdown` strips, everywhere
`scrub` already runs rather than only in the one field that has its own `transformations` slot.
"""
from __future__ import annotations

from typing import Any

# C0 controls and DEL, mapped to the Unicode Control Pictures block: 0x00-0x1F -> U+2400-U+241F,
# DEL -> U+2421. So ESC becomes a visible `␛`.
#
# REPLACED, NOT DROPPED: a result that silently rewrote what Google said would be wrong about
# the record. A visible marker also says something true and useful on its own - that this text
# arrived carrying a terminal escape, which is not something an ordinary subject line does by
# accident.
_CONTROLS = {code: chr(0x2400 + code) for code in range(0x20) if code not in (0x09, 0x0A, 0x0D)}
_CONTROLS[0x7F] = "\N{SYMBOL FOR DELETE}"

# The Trojan-Source bidi-override pair and BOM - the same two classes `_markdown._STRIP_RULES`
# strips from a message body, applied here so every OTHER field (subject, display name,
# filename, event summary, attendee name) gets the same defence rather than only the body.
# Mapped to U+FFFD (REPLACEMENT CHARACTER), not a Control Pictures glyph - there is no picture
# glyph for these codepoints, and U+FFFD is the standard "something was here and is elided"
# marker, itself inert (it has no bidi or byte-order behaviour of its own), so a defused
# override cannot still reorder the text around it. Kept in a SEPARATE mapping from `_CONTROLS`
# rather than merged into it, so `suspicious_count`/callers could in principle distinguish the
# two classes the way `_markdown._STRIP_RULES` keeps its three apart (DEC-021) - not exercised
# today (this module counts uniformly, unlike `_markdown.rules_fired`), but the separation costs
# nothing and keeps the door open.
_TROJAN_SOURCE = {0x202D: "�", 0x202E: "�", 0xFEFF: "�"}

# The combined defusal table `neutralise`/`suspicious_count` actually apply.
_DEFUSE = {**_CONTROLS, **_TROJAN_SOURCE}


def neutralise(text: str) -> str:
    """Return `text` with terminal control characters and Trojan-Source bidi-override/BOM
    codepoints made visible and inert."""
    return text.translate(_DEFUSE)


def _suspicious_in_str(text: str) -> int:
    return sum(1 for ch in text if ord(ch) in _DEFUSE)


def suspicious_count(value: Any) -> int:
    """How many codepoints `scrub(value)` would neutralise - the disclosure this seam owes,
    the same way `mail._render_body`'s `transformations` list discloses what
    `_markdown.strip_suspicious` changed for the one field that has a schema slot for it
    (DEC-021: disclose what you changed). Every OTHER field scrub covers has no such slot, so
    the count is what `_tools._base` logs at the tool-result seam instead - metadata, not
    content, the same discipline `_refused` already holds for a refusal's exception type and
    duration.

    Walks the same structure `scrub` does (dict keys included - a key can be a Google-supplied
    label or header name, displayed like any other string), computed separately rather than
    threaded through `scrub` itself so `scrub`'s own recursion stays a plain rebuild with
    nothing to unpack at each call site.
    """
    if isinstance(value, str):
        return _suspicious_in_str(value)
    if isinstance(value, dict):
        return sum(suspicious_count(k) + suspicious_count(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return sum(suspicious_count(item) for item in value)
    return 0


def scrub(value: Any) -> Any:
    """Walk a tool result, neutralising every string in it.

    Containers are rebuilt rather than mutated in place, because a backend method may hand
    back a structure shared with something the caller still holds.

    Non-string leaves - ints, bools, None - are returned as they are. Dict KEYS are scrubbed
    too: a key can be a Google-supplied label name or header name, and a key is displayed like
    any other string.
    """
    if isinstance(value, str):
        return neutralise(value)
    if isinstance(value, dict):
        return {scrub(key): scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(scrub(item) for item in value)
    return value
