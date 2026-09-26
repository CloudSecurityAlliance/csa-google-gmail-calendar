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


def neutralise(text: str) -> str:
    """Return `text` with terminal control characters made visible and inert."""
    return text.translate(_CONTROLS)


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
