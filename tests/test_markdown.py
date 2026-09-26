"""`_markdown.to_markdown` directly: the never-rendered tag strip and the concealed-but-empty
case, both upstream of the higher-level `Mail`/`FakeBackend` tests in
`test_message_parsing.py`."""
from csa_google_gmail_calendar import _markdown


def test_a_script_tag_is_removed_and_not_reported_as_hidden():
    """`_NEVER_RENDERED` tags (script/style/template/noscript/head) never reach a reader
    regardless of any `style=` attribute - they are decomposed outright, not routed through the
    concealed-text disclosure path, so their content must not appear in the output OR in
    `hidden_texts` (which is for concealment, not for ordinary non-content markup)."""
    markdown, hidden_texts, _fired = _markdown.to_markdown(
        "<p>visible</p><script>evil.exe();secret_payload();</script>")
    assert "visible" in markdown
    assert "secret_payload" not in markdown
    assert hidden_texts == []


def test_a_concealed_element_with_no_text_produces_no_hidden_entry():
    """A `display:none` element that has no text at all (empty, or only whitespace) must still
    be removed from the output, but has nothing to disclose - `hidden_texts` must not gain a
    blank entry for it."""
    markdown, hidden_texts, _fired = _markdown.to_markdown(
        '<p>visible</p><span style="display:none">   </span>')
    assert "visible" in markdown
    assert hidden_texts == []
