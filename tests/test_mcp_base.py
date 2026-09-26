"""`_tools/_base.py` in isolation: the error ladder (sync and async), and unknown-argument
refusal (sync and async) - exercised directly rather than only through the auth tools, so every
branch of the ladder is demonstrated once, in one place, regardless of which real tool a future
task adds.
"""
import anyio
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from csa_google_gmail_calendar import exceptions as exc
from csa_google_gmail_calendar.mcp._tools import _base


def _run(coro):
    return anyio.run(lambda: coro)


@pytest.mark.parametrize("raised,expect", [
    (exc.PolicyError("no such capability"), "no such capability"),
    (exc.NotFoundError("message m1 not found"), "not found: message m1 not found"),
    (exc.AccessError("not permitted"), "permission denied: not permitted"),
    (exc.AuthError("no cached credentials"), "no cached credentials"),
    (exc.ConflictError("etag mismatch"), "conflict: etag mismatch"),
    (exc.UnsupportedOperation("not implemented here"), "not implemented here"),
    (exc.ApiError("Google API error 429"), "Google rejected the request: Google API error 429"),
    (ValueError("bad order_by"), "invalid argument: bad order_by"),
])
def test_errors_translates_every_rung_of_the_ladder_sync(raised, expect):
    def fn():
        raise raised
    wrapped = _base._errors(fn)
    with pytest.raises(ToolError, match=expect.replace("(", r"\(").replace(")", r"\)")):
        wrapped()


def test_errors_reraises_an_untranslated_exception_sync():
    def fn():
        raise RuntimeError("a genuine bug")
    wrapped = _base._errors(fn)
    with pytest.raises(RuntimeError, match="a genuine bug"):
        wrapped()


def test_errors_returns_a_scrubbed_result_on_success_sync():
    def fn():
        return {"subject": "hi\x1b[2Kbye"}
    wrapped = _base._errors(fn)
    out = wrapped()
    assert "\x1b" not in out["subject"]
    assert "␛" in out["subject"]


def test_errors_scrubs_a_bidi_override_out_of_a_successful_result_sync():
    """FIX 3 (final whole-branch review): a field with no `transformations` slot of its own -
    e.g. an `AttachmentRef.filename` - must still lose a Trojan-Source bidi override on the
    way out, not just C0/DEL."""
    def fn():
        return {"filename": "invoice‮fdp.exe"}
    wrapped = _base._errors(fn)
    out = wrapped()
    assert "‮" not in out["filename"]


def test_errors_scrubs_the_refusal_message_too_sync():
    """FIX 2 (final whole-branch review): `_attachments._echo` deliberately echoes a
    caller-supplied path into a `PolicyError`, which renders in a terminal with any control
    sequence it carries intact unless this seam scrubs it - exactly the human/model asymmetry
    `_untrusted.py` exists to close, reachable through a refusal instead of a result."""
    def fn():
        raise exc.PolicyError("'evil\x1b[2Kpath.txt' resolves outside the root")
    wrapped = _base._errors(fn)
    with pytest.raises(ToolError) as excinfo:
        wrapped()
    assert "\x1b" not in str(excinfo.value)
    assert "␛" in str(excinfo.value)


def test_errors_scrubs_a_bidi_override_out_of_a_refusal_message_sync():
    def fn():
        raise exc.PolicyError("'invoice‮fdp.exe' resolves outside the root")
    wrapped = _base._errors(fn)
    with pytest.raises(ToolError) as excinfo:
        wrapped()
    assert "‮" not in str(excinfo.value)


def test_errors_logs_the_suspicious_count_for_a_scrubbed_success_sync(caplog):
    def fn():
        return {"filename": "invoice‮fdp.exe"}
    wrapped = _base._errors(fn)
    with caplog.at_level("INFO", logger="csa_google_gmail_calendar.mcp._tools._base"):
        wrapped()
    assert any("suspicious character" in r.message for r in caplog.records)


def test_errors_logs_the_suspicious_count_for_a_scrubbed_refusal_sync(caplog):
    def fn():
        raise exc.PolicyError("'invoice‮fdp.exe' resolves outside the root")
    wrapped = _base._errors(fn)
    with caplog.at_level("INFO", logger="csa_google_gmail_calendar.mcp._tools._base"):
        with pytest.raises(ToolError):
            wrapped()
    assert any("suspicious character" in r.message for r in caplog.records)


def test_errors_does_not_log_a_count_when_nothing_is_suspicious_sync(caplog):
    def fn():
        return {"subject": "ordinary subject"}
    wrapped = _base._errors(fn)
    with caplog.at_level("INFO", logger="csa_google_gmail_calendar.mcp._tools._base"):
        wrapped()
    assert not any("suspicious character" in r.message for r in caplog.records)


@pytest.mark.parametrize("raised,expect", [
    (exc.PolicyError("no such capability"), "no such capability"),
    (exc.NotFoundError("event e1 not found"), "not found: event e1 not found"),
    (exc.ApiError("Google API error 500"), "Google rejected the request: Google API error 500"),
    (ValueError("bad time range"), "invalid argument: bad time range"),
])
def test_errors_translates_every_rung_of_the_ladder_async(raised, expect):
    async def fn():
        raise raised
    wrapped = _base._errors(fn)

    async def call():
        await wrapped()
    with pytest.raises(ToolError, match=expect.replace("(", r"\(").replace(")", r"\)")):
        _run(call())


def test_errors_reraises_an_untranslated_exception_async():
    async def fn():
        raise RuntimeError("a genuine bug")
    wrapped = _base._errors(fn)

    async def call():
        await wrapped()
    with pytest.raises(RuntimeError, match="a genuine bug"):
        _run(call())


def test_errors_returns_a_scrubbed_result_on_success_async():
    async def fn():
        return ["ok\x1b[2K"]
    wrapped = _base._errors(fn)
    out = _run(wrapped())
    assert "\x1b" not in out[0]


def test_errors_scrubs_the_refusal_message_too_async():
    """FIX 2, async branch - the two branches of `_errors` share `_refused`, but each has its
    own `except`/`return` wiring, so both are exercised directly rather than assuming parity."""
    async def fn():
        raise exc.PolicyError("'evil\x1b[2Kpath.txt' resolves outside the root")
    wrapped = _base._errors(fn)

    async def call():
        await wrapped()
    with pytest.raises(ToolError) as excinfo:
        _run(call())
    assert "\x1b" not in str(excinfo.value)


def test_refuse_unknown_arguments_passes_through_declared_arguments_sync():
    def fn(a: int, b: str = "x") -> dict:
        """doc"""
        return {"a": a, "b": b}
    wrapped = _base._refuse_unknown_arguments(fn)
    assert wrapped(a=1) == {"a": 1, "b": "x"}


def test_refuse_unknown_arguments_refuses_an_unknown_keyword_sync():
    def fn(a: int) -> dict:
        """doc"""
        return {"a": a}
    wrapped = _base._refuse_unknown_arguments(fn)
    with pytest.raises(ToolError, match="unknown argument"):
        wrapped(a=1, bogus="y")


def test_refuse_unknown_arguments_refuses_an_unknown_keyword_async():
    async def fn(a: int) -> dict:
        """doc"""
        return {"a": a}
    wrapped = _base._refuse_unknown_arguments(fn)

    async def call():
        return await wrapped(a=1, bogus="y")
    with pytest.raises(ToolError, match="unknown argument"):
        _run(call())


def test_refuse_unknown_arguments_passes_through_declared_arguments_async():
    async def fn(a: int) -> dict:
        """doc"""
        return {"a": a}
    wrapped = _base._refuse_unknown_arguments(fn)
    assert _run(wrapped(a=1)) == {"a": 1}


def test_tool_registers_the_composed_function_under_the_given_name():
    app = MCPServer(name="x", version="0.0.0")

    @_base.tool(app, annotations=_base.READ, name="renamed")
    def original(a: int) -> dict:
        """doc"""
        return {"a": a}

    registered = app._tool_manager.get_tool("renamed")
    assert registered is not None
    assert registered.fn(a=2) == {"a": 2}
    with pytest.raises(ToolError, match="unknown argument"):
        registered.fn(a=2, bogus=1)
