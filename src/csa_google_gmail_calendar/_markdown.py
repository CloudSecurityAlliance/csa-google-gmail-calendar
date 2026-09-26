"""Gmail HTML becomes Markdown here, and nowhere else.

DEC-018 (csa-zendesk): when content crosses to a model, Markdown is the
representation. This module is the whole transform for
csa-google-gmail-calendar, and it is PURE - no HTTP, no tokens, no filesystem -
so it can be tested exhaustively offline.

Ported from csa-zendesk's `_markdown.py` (same problem, same fix - Gmail's
`text/html` MIME part is the same "trust the real rendering path, not a
flattened stand-in" case DEC-018 was written for). `mail.py` calls
`to_markdown` only on the `text/html` part it picked as the message body -
never on the whole raw message - so what reaches this module is already just
the HTML a mail client would have rendered.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

# Elements whose text is never shown to a reader. Removed before conversion so
# their content cannot arrive as ordinary prose. `script`/`style` are dropped by
# markdownify anyway; naming them here means the removal does not depend on that.
_NEVER_RENDERED = ("script", "style", "template", "noscript", "head")

#: Inline-style declarations that mean "a reader will not see this".
#: INLINE STYLES ONLY, deliberately: a `<style>` block's selectors need a
#: cascade to resolve, which is the open problem this does not pretend to
#: solve. Under-reporting is the honest failure direction.
HIDING_RULES: tuple[str, ...] = (
    r"display\s*:\s*none",
    r"visibility\s*:\s*hidden",
    r"font-size\s*:\s*0(?![.\d])",
    r"(?:max-)?height\s*:\s*0(?![.\d])",
    r"opacity\s*:\s*0(?![.\d])",
    r"(?:left|top|text-indent)\s*:\s*-\d{3,}",
)
_HIDDEN = re.compile("|".join(HIDING_RULES), re.I)

#: Codepoints stripped because they have no legitimate use in prose. STRIPPED,
#: never folded (see `strip_suspicious`'s docstring on why folding is worse).
#:
#: Corrected 2026-09-22: a first version of this set was written from the
#: threat list alone (zero-width space, ZWNJ, ZWJ, LRM/RLM, bidi EMBEDDING,
#: bidi isolates, tag characters, soft hyphen, Mongolian vowel separator) and
#: never checked against a legitimacy list. Measured against real script
#: usage, it damaged seven legitimate cases: Persian ZWNJ is SEMANTIC
#: (می‌رود "mi-ravad" vs میرود are different words), ZWJ is required for
#: Arabic letter shaping, Indic conjuncts and emoji ZWJ sequences (a family
#: emoji decomposes into three separate people without it), LRM/RLM are
#: legitimate directional marks, bidi EMBEDDING (as opposed to override) is
#: ordinary right-to-left text, and tag characters compose subdivision-flag
#: emoji (Scotland's flag becomes a plain black flag without them). None of
#: that is stripped now.
#:
#: Corrected again 2026-09-22 (round 2): WORD JOINER (0x2060) was added to
#: this set without being checked against a legitimacy corpus either - the
#: same defect reproduced at small scale. It is the functional complement of
#: ZWSP (ZWSP permits a break where none would otherwise occur; WJ suppresses
#: one that otherwise would happen), so the same reasoning that got ZWSP
#: dropped applies to WJ: it binds a number to its unit, holds an
#: abbreviation together, and prevents a mid-token break in technical or
#: legal text. Also not stripped now.
#:
#: The rest of the original threat list stays off for reasons of its own,
#: not just "it was on the list": bidi ISOLATES (0x2066-0x2069, LRI/RLI/FSI/
#: PDI) are the modern, recommended replacement for the 0x202A-0x202C
#: embedding controls - they scope directionality without the leakage
#: embeddings suffer, and are ordinary in mixed-direction text. Soft hyphen
#: (0x00AD) is a legitimate hyphenation hint, invisible unless the renderer
#: actually breaks there, and ubiquitous in justified text produced by word
#: processors. The Mongolian vowel separator (0x180E) is a format control
#: that selects Mongolian vowel/letter presentation forms, functionally
#: similar to ZWNJ - not "legitimate in Mongolian script" on its own, which
#: states legitimacy without saying what the character does.
#:
#: What remains: the Trojan Source bidi-OVERRIDE pair (LRO/RLO reorder how
#: text renders without reordering the source, which is how `invoice[RLO]
#: fdp.exe` displays as `invoice.exe`, with no legitimate prose use); U+FEFF,
#: whose word-joiner role Unicode formally deprecated in version 3.2 (2002)
#: when it introduced U+2060 (WORD JOINER, above) specifically to take that
#: role over - so U+FEFF has no SURVIVING legitimate use, unlike U+2060,
#: which is exactly why one is stripped here and the other is not; and C0
#: controls other than tab/LF/CR.
#:
#: Consequence, not an oversight: this no longer defangs the observed
#: zero-width-signature attack (U+200C interleaved through a name), because
#: U+200C cannot be stripped unconditionally without breaking Persian and
#: Indic text. Catching that needs a density-based detector, deliberately out
#: of scope here - see
#: `test_a_zero_width_signature_attack_is_NOT_defanged_by_stripping`.
#: The three classes in `_STRIP`, kept apart because they are not the same
#: signal to a reader (DEC-021). A Trojan Source override being present in a
#: support ticket is worth attention; a stray control character is
#: housekeeping. Collapsing them into one "stripped" rule would flatten a
#: finding into a non-finding.
#:
#: Ordered most- to least-interesting, and `rules_fired` reports in this order
#: so the first entry is the one worth reading.
_STRIP_RULES: tuple[tuple[str, frozenset[int]], ...] = (
    ("codepoint/bidi-override", frozenset({0x202D, 0x202E})),
    ("codepoint/byte-order-mark", frozenset({0xFEFF})),
    ("codepoint/control-character", frozenset(c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D))),
)


def rules_fired(text: str) -> list[str]:
    """Which `_STRIP` classes are present in `text`, most interesting first.

    Reports what `strip_suspicious` WOULD remove, so a caller can disclose it
    (DEC-021) without diffing before and after - a diff says a change happened
    and cannot say which class it belonged to.
    """
    present = {ord(ch) for ch in text}
    return [name for name, points in _STRIP_RULES if present & points]


_STRIP = (
    {0x202D, 0x202E}  # LEFT-TO-RIGHT OVERRIDE, RIGHT-TO-LEFT OVERRIDE - Trojan Source
    | {0xFEFF}  # BOM - word-joiner role deprecated by Unicode 3.2 (2002) in favour
    #            of U+2060; no surviving legitimate use (contrast U+2060, not stripped)
    | frozenset(c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D))  # controls
)


def strip_suspicious(text: str) -> str:
    """Remove codepoints that have no legitimate use in prose.

    Strips: the Trojan Source bidi-override pair (LRO/RLO), a mid-document
    BOM, and C0 controls other than tab/LF/CR.

    Deliberately NOT stripped, because each has a real, meaning-bearing use
    somewhere in ordinary text: ZWSP (Thai/Khmer word segmentation), ZWNJ
    (semantic in Persian and Indic scripts), ZWJ (Arabic letter shaping,
    Indic conjuncts, emoji ZWJ sequences), LRM/RLM (legitimate directional
    marks), bidi EMBEDDING codepoints (ordinary right-to-left text, as
    opposed to override), bidi ISOLATES (the modern, recommended replacement
    for embedding - they scope directionality without embedding's leakage),
    tag characters (subdivision-flag emoji), soft hyphen (a hyphenation
    hint, invisible unless the renderer actually breaks there), the
    Mongolian vowel separator (a format control that selects Mongolian
    vowel/letter presentation forms, functionally similar to ZWNJ), and the
    WORD JOINER (the functional complement of ZWSP - it suppresses a break
    rather than permitting one, e.g. binding a number to its unit or holding
    an abbreviation together). A broader first version of this set stripped
    ZWSP, ZWNJ, ZWJ, LRM/RLM, bidi EMBEDDING, tag characters, bidi isolates,
    soft hyphen and the Mongolian vowel separator, and was measured to
    damage seven languages/scripts; a second pass added WORD JOINER without
    the same check and had to be corrected again. This is what survived
    checking against a legitimacy list, not just a threat list. One
    consequence of that: this function does NOT defang the
    zero-width-signature attack (U+200C interleaved through a name) - see
    `test_a_zero_width_signature_attack_is_NOT_defanged_by_stripping`.

    Residual, recorded but not pinned by a test (Minor 5, final whole-branch
    review, TODO.md H4): bidi ISOLATES (U+2066-U+2069) and EMBEDDINGS
    (U+202A-U+202C) are part of the Trojan Source family too - they produce
    RUN-LEVEL visual reordering rather than the character-level reversal the
    stripped OVERRIDE pair (LRO/RLO) produces - and excluding them was correct
    on the same legitimacy grounds as everything else in this docstring, but
    unlike U+200C above it has no pinning test, no README paragraph and no
    CHANGELOG line of its own - only this sentence and TODO.md H4.

    NOT a homoglyph check. A rule that flags mixed scripts within a word also
    flags Indigenous orthographies (Musqueam contains a Greek theta, because
    IPA-derived characters are its standard written form) and IPA
    transcriptions. That rule is measured at 6-of-8 false positives against
    legitimate content and is deliberately absent here.
    """
    return "".join(ch for ch in text if ord(ch) not in _STRIP)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def to_markdown(html: str) -> tuple[str, list[str], list[str]]:
    """Convert `html` to Markdown, separating text a reader would not see.

    Returns `(markdown, hidden_texts)`. Hidden text is REMOVED from the Markdown
    and returned alongside it - never silently dropped, never emitted as
    ordinary prose. Concealment is the signal; the caller decides what to do
    with it.

    KNOWN GAP: white-on-white is not detected. Whether text matches its
    background needs the ancestor's background - the cascade - and a rule on the
    element's own `style` cannot see it. Measured: a naive pre-pass closes
    display:none and zero-size and misses this one.
    """
    soup = _soup(html)
    for element in soup.find_all(_NEVER_RENDERED):
        element.decompose()

    # Collect first, THEN decompose. Decomposing inside find_all() invalidates
    # elements the iterator has not reached, which comes back as attrs=None
    # several elements later.
    concealed = [
        element
        for element in soup.find_all(style=True)
        if element.attrs and _HIDDEN.search(str(element.attrs.get("style") or ""))
    ]
    hidden_texts = []
    for element in concealed:
        text = element.get_text(" ", strip=True)
        if text:
            hidden_texts.append(text)
        element.decompose()

    markdown = MarkdownConverter().convert_soup(soup)
    cleaned = markdown.strip()
    # Report against the CONVERTED text, not the source HTML. A codepoint inside
    # a `<script>` never reaches the output - it went with the decomposed
    # element - so reporting from the source would claim a strip that did not
    # happen.
    fired = rules_fired(cleaned) + [r for t in hidden_texts for r in rules_fired(t)]
    return (
        strip_suspicious(cleaned),
        [strip_suspicious(t) for t in hidden_texts],
        sorted(set(fired), key=lambda r: [n for n, _ in _STRIP_RULES].index(r)),
    )
