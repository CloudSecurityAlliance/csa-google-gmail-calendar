"""`_tools/_base.py` in isolation: the error ladder (sync and async), and unknown-argument
refusal (sync and async) - exercised directly rather than only through the auth tools, so every
branch of the ladder is demonstrated once, in one place, regardless of which real tool a future
task adds.
"""
import anyio
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
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
        return {"filename": "invoice\u202efdp.exe"}
    wrapped = _base._errors(fn)
    out = wrapped()
    assert "\u202e" not in out["filename"]


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
        raise exc.PolicyError("'invoice\u202efdp.exe' resolves outside the root")
    wrapped = _base._errors(fn)
    with pytest.raises(ToolError) as excinfo:
        wrapped()
    assert "\u202e" not in str(excinfo.value)


def test_errors_logs_the_suspicious_count_for_a_scrubbed_success_sync(caplog):
    def fn():
        return {"filename": "invoice\u202efdp.exe"}
    wrapped = _base._errors(fn)
    with caplog.at_level("INFO", logger="csa_google_gmail_calendar.mcp._tools._base"):
        wrapped()
    assert any("suspicious character" in r.message for r in caplog.records)


def test_errors_logs_the_suspicious_count_for_a_scrubbed_refusal_sync(caplog):
    def fn():
        raise exc.PolicyError("'invoice\u202efdp.exe' resolves outside the root")
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
    (exc.AccessError("not permitted"), "permission denied: not permitted"),
    (exc.AuthError("no cached credentials"), "no cached credentials"),
    (exc.ConflictError("etag mismatch"), "conflict: etag mismatch"),
    (exc.UnsupportedOperation("not implemented here"), "not implemented here"),
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


# ── SDK-injected parameters ─────────────────────────────────────────
#
# The defect these pin down shipped in 0.1.0: `authenticate` takes `ctx: Context` so it can call
# `ctx.elicit_url(...)`, FastMCP strips a `Context` parameter from the JSON schema (no client
# supplies one), `_refuse_unknown_arguments` computed its accepted set FROM that schema, and the
# SDK then injects `ctx` **as a keyword argument** - so the guard refused the SDK's own injection
# and the tool could not be called at all through the real transport.
#
# Every existing test passed `ctx` POSITIONALLY (`fn(ctx, force=True)`, see `test_auth_tool.py`'s
# `_call_async`), and the guard only ever inspected `kwargs` - so the single convention the real
# transport uses was the one convention no test used. The line was covered on every run. Passing
# `ctx` by KEYWORD is the entire point of the two tests below; a later tidy-up into a positional
# call would silently restore the blind spot.

def test_refuse_unknown_arguments_allows_a_context_the_sdk_injects_by_keyword_async():
    async def fn(ctx: Context, a: int = 1) -> dict:
        """doc"""
        return {"a": a, "ctx": ctx}

    # Without this the test would be vacuous: it only proves anything if `ctx` really is absent
    # from the schema the guard derives its accepted set from.
    assert "ctx" not in _base._declared_properties(fn)

    wrapped = _base._refuse_unknown_arguments(fn)
    assert _run(wrapped(ctx="injected", a=2)) == {"a": 2, "ctx": "injected"}


def test_refuse_unknown_arguments_allows_a_context_the_sdk_injects_by_keyword_sync():
    def fn(ctx: Context, a: int = 1) -> dict:
        """doc"""
        return {"a": a, "ctx": ctx}

    assert "ctx" not in _base._declared_properties(fn)

    wrapped = _base._refuse_unknown_arguments(fn)
    assert wrapped(ctx="injected", a=2) == {"a": 2, "ctx": "injected"}


def test_refuse_unknown_arguments_still_refuses_a_bogus_keyword_on_a_context_tool():
    """The fix widens the accepted set by exactly the injected names, not by "anything goes" -
    a tool with a `Context` parameter must still refuse an argument nobody declared."""
    async def fn(ctx: Context, a: int = 1) -> dict:
        """doc"""
        return {"a": a}

    wrapped = _base._refuse_unknown_arguments(fn)

    async def call():
        return await wrapped(ctx="injected", a=2, bogus="y")
    with pytest.raises(ToolError, match="unknown argument"):
        _run(call())


def test_a_context_tool_survives_the_sdks_own_call_path():
    """The end-to-end shape of the 0.1.0 defect, exercised through `ToolManager.call_tool` -
    the path the real MCP transport uses - rather than through `.fn(...)`.

    This is the test that would have caught it. Every other test in this repo reaches a tool by
    `server._tool_manager.get_tool(name).fn(**kw)`, which is deliberate (see this module's
    docstring: the invariants must live in the function those tests call). The cost, unnoticed
    until a real client called `authenticate` and got a refusal, is that `.fn(...)` lets the test
    choose how `ctx` is passed while the SDK does not - so a guard keyed on `kwargs` looked
    correct under every test and was wrong in production. One test on the real path covers the
    whole class; it is cheap, and it does not replace the direct-`fn` tests, it backstops them.
    """
    app = MCPServer(name="probe", version="0.0.0")

    @_base.tool(app, annotations=_base.READ)
    async def needs_context(ctx: Context, a: int = 1) -> dict:
        """doc"""
        return {"a": a}

    # Vacuity guard, as above: this proves something only while `ctx` is absent from the schema,
    # which is what makes the SDK inject it instead of passing it through as an argument.
    assert "ctx" not in (app._tool_manager.get_tool("needs_context").parameters
                         or {}).get("properties", {})

    async def call():
        return await app._tool_manager.call_tool("needs_context", {"a": 7}, None)
    assert _run(call()) == {"a": 7}
