"""Shared machinery for the tool modules: error translation, unknown-argument refusal,
annotations.

Adapted from `../csa-google-workspace/src/csa_google_workspace/mcp/_tools/_base.py`'s `_errors`
decorator - the comments there record *why* each piece exists, and that reasoning is carried
across, adapted to this package's exception hierarchy (`exceptions.py` has no `ReadOnlyError`;
it has `PolicyError`, which the sibling project does not).

## The two invariants every tool must keep

**Raise the SDK's `ToolError`, never a plain exception.** Anything else becomes an
`UnexpectedToolError` whose message the SDK deliberately suppresses, so the caller would see
"Error executing tool send_message" and nothing about what actually went wrong.

**Never log tool arguments.** A message body, a recipient list, an event summary are all
content. Our stderr is persisted by the MCP client into a cache directory we cannot see or
purge (`_logging.py`), so logging arguments "for debugging" is how untrusted content becomes a
durable artifact outside its own governance.

## What `tool()` composes, and in what order

    tool(app, annotations=WRITE)(fn)
        -> app.tool(annotations=WRITE)(_refuse_unknown_arguments(fn)(_errors(fn)))

`_refuse_unknown_arguments` wraps OUTSIDE `_errors`, deliberately: it raises `ToolError`
directly and must never reach `_errors`'s own `except Exception` fallback, which logs at ERROR
on the theory that anything reaching it is a bug in the ladder rather than a caller mistake. An
unknown argument is the caller's mistake, not this server's, and belongs in the same INFO-level
"refused" bucket every other policy/not-found/invalid-argument refusal lands in.

Both wrap the RAW function, before `app.tool()` ever sees it. That is load-bearing, not
cosmetic: `tests/test_mail_tools.py` (task 11) and `tests/test_calendar_tools.py` (task 12) call
`server._tool_manager.get_tool(name).fn(**kw)` directly, bypassing the SDK's own call path (and
its pydantic argument validation) entirely - so the invariants above have to live IN the
function those tests call, not in a layer only the real MCP transport goes through.
"""
from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool as _InternalTool
from mcp.types import Tool as _WireTool
from mcp.types import ToolAnnotations

from ... import exceptions as exc
from .. import _untrusted

log = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

# `open_world_hint=True` on every one of them, per this project's own primary named risk:
# message bodies, event summaries and attendee names are all written by somebody else, and
# prompt injection through them is what these annotations exist to flag. It is on the WRITES
# too, deliberately - a write returns Google's response (an id, an updated event body), so the
# reply is third-party content even when the request was not, and a distinction that has to be
# re-derived per tool is one that drifts.
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                       open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                        open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=True)

# The one read this server has that is NOT open-world: `auth_status` (`_tools/auth.py`) makes
# no network call and returns no Google-authored content at all - only this process's own
# computed state about a local token file. `open_world_hint=True` on every OTHER tool here
# is doing real work (it tells a client "scrutinise this, it's from an open world of external
# entities, including strangers writing message bodies and event summaries"); repeating it on
# a tool for which it is simply false would not be conservative, it would be inaccurate, and an
# annotation that is uniformly true on every tool carries no information at all. Matches
# csa-zendesk's own `auth_status` precedent.
LOCAL_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)


def _took(started: float) -> str:
    return f"{(time.monotonic() - started) * 1000:.0f}ms"


def _refused(fn: Callable[..., Any], started: float, cause: BaseException,
            translated: ToolError) -> ToolError:
    """Record an expected refusal and hand back the `ToolError` to raise.

    INFO rather than WARNING: a policy refusal, a missing message, or a bad argument is the
    system working as designed. Logging them as warnings would make a correctly-configured
    server look unhealthy, and a log that cries wolf gets filtered out - taking the real
    warnings with it.

    **The MESSAGE is deliberately not logged.** `PolicyError` names a capability;
    `NotFoundError` names a message/thread/event id; a refused attachment path echoes the
    caller's string. Those are genuinely useful to the caller in the one reply they get, but the
    log is a DIFFERENT destination with different governance - a cache directory this server
    cannot rotate, purge, or even read. So the caller keeps the full message and the log gets
    only the exception type.
    """
    log.info("%s refused after %s: %s", fn.__name__, _took(started), type(cause).__name__)
    return translated


def _errors(fn: _F) -> _F:
    """Translate the library's typed exceptions into readable tool errors, and record the call.

    See the module docstring for the two invariants this holds. What is logged is deliberately
    thin: the **tool name**, the **outcome**, and the **duration** - never the arguments, and
    (via `_refused`) never the message text of an expected refusal either.

    **Every result also passes through `_untrusted.scrub`**, for the same reason this is the
    one place every tool passes through: it is the one place a call can be recorded, and a
    result sanitised, without touching every handler - including one added later. That removes
    terminal control sequences from returned strings, which JSON escaping does not (see
    `_untrusted.py`).

    Handles both sync and async tool bodies - `authenticate` (task 10) awaits `ctx.elicit_url`
    and `anyio.to_thread.run_sync`, so it cannot be a plain sync function, while every mail and
    calendar tool (tasks 11/12) is sync. `functools.wraps` on both branches keeps `__wrapped__`
    so the SDK (and this module's own `_declared_properties`) can still read the real signature.
    """
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except exc.PolicyError as e:
                raise _refused(fn, started, e, ToolError(str(e))) from e
            except exc.NotFoundError as e:
                raise _refused(fn, started, e, ToolError(f"not found: {e}")) from e
            except exc.AccessError as e:
                raise _refused(fn, started, e, ToolError(f"permission denied: {e}")) from e
            except exc.AuthError as e:
                raise _refused(fn, started, e, ToolError(str(e))) from e
            except exc.ConflictError as e:
                raise _refused(fn, started, e, ToolError(f"conflict: {e}")) from e
            except exc.UnsupportedOperation as e:
                raise _refused(fn, started, e, ToolError(str(e))) from e
            except exc.ApiError as e:
                # `backend._map_http_error` already keeps this to Google's own parsed
                # `error.message`, never the raw response body - see that function's
                # docstring for why a raw body can echo request content.
                raise _refused(fn, started, e,
                               ToolError(f"Google rejected the request: {e}")) from e
            except ValueError as e:
                raise _refused(fn, started, e, ToolError(f"invalid argument: {e}")) from e
            except Exception as e:
                log.error("%s failed after %s: %s", fn.__name__, _took(started),
                          type(e).__name__)
                raise
            log.info("%s ok in %s", fn.__name__, _took(started))
            return _untrusted.scrub(result)
        return awrapped  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except exc.PolicyError as e:
            raise _refused(fn, started, e, ToolError(str(e))) from e
        except exc.NotFoundError as e:
            raise _refused(fn, started, e, ToolError(f"not found: {e}")) from e
        except exc.AccessError as e:
            raise _refused(fn, started, e, ToolError(f"permission denied: {e}")) from e
        except exc.AuthError as e:
            raise _refused(fn, started, e, ToolError(str(e))) from e
        except exc.ConflictError as e:
            raise _refused(fn, started, e, ToolError(f"conflict: {e}")) from e
        except exc.UnsupportedOperation as e:
            raise _refused(fn, started, e, ToolError(str(e))) from e
        except exc.ApiError as e:
            raise _refused(fn, started, e, ToolError(f"Google rejected the request: {e}")) from e
        except ValueError as e:
            raise _refused(fn, started, e, ToolError(f"invalid argument: {e}")) from e
        except Exception as e:
            # Not translated - re-raised as-is. Recorded at ERROR because everything above is
            # the system working and this is not: an untranslated exception is a bug in the
            # ladder above, not a caller mistake.
            log.error("%s failed after %s: %s", fn.__name__, _took(started), type(e).__name__)
            raise
        log.info("%s ok in %s", fn.__name__, _took(started))
        return _untrusted.scrub(result)
    return wrapped  # type: ignore[return-value]


def _declared_properties(fn: Callable[..., Any]) -> frozenset[str]:
    """The JSON-Schema property names `fn` will be registered with, computed the same way
    `MCPServer.list_tools()` computes them for the wire - never a hand-maintained duplicate
    schema that could drift from what `app.tool()` actually derives from the function.

    **Verified against the installed `mcp` package (2.2.0) rather than assumed.** The
    *internal* registration object (`mcp.server.mcpserver.tools.base.Tool`, what
    `app._tool_manager.get_tool(...)` returns) names this field `.parameters`. The *wire*
    object (`mcp.types.Tool`, what a client actually receives from `list_tools`) names the same
    JSON Schema `.input_schema` in Python - snake_case despite `inputSchema` being the name on
    the wire (a pydantic alias; `MCPServer.list_tools()` builds one via
    `MCPTool(..., input_schema=info.parameters, ...)`). Getting this backwards - reading
    `.input_schema` off the *internal* object, which does not have that attribute, or
    `.get("input_schema")` on something that is a dict under a different key - yields an empty
    set and refuses *every* argument as unknown; csa-zendesk hit exactly that (41 tests failed
    instantly) from the equivalent mistake in a hand-rolled schema dict. Building the same
    `mcp.types.Tool` the SDK itself builds, rather than reaching for either attribute name by
    guesswork, is what this function does to avoid repeating it.
    """
    internal = _InternalTool.from_function(fn)
    spec = _WireTool(name=internal.name, title=internal.title, description=internal.description,
                     input_schema=internal.parameters, output_schema=internal.output_schema,
                     annotations=internal.annotations, icons=internal.icons, _meta=internal.meta)
    return frozenset((spec.input_schema or {}).get("properties", {}))


def _refuse_unknown_arguments(fn: _F) -> _F:
    """Refuse a call naming an argument `fn`'s own schema does not declare, rather than
    silently dropping it.

    Needed because the installed SDK does not do this itself: its generated pydantic argument
    model accepts and silently discards an unrecognised keyword (verified directly against
    mcp 2.2.0 - a call with a bogus extra argument succeeds and the extra key is simply gone),
    so a caller who mistypes an argument name, or a model that invents one the schema never
    declared, gets no signal that anything was wrong. A silently-dropped argument is a
    silently-wrong call - `bcc` accepted and ignored on `send_message` is not the same failure
    mode as `bcc` refused, and only one of them is safe to leave unnoticed.

    The accepted set is computed ONCE, from the raw function, before `_errors` or `app.tool()`
    ever see it (`_declared_properties`) - so it reflects exactly the arguments the tool will
    be registered with, never a hand-maintained second copy that could drift from it.
    """
    accepted = _declared_properties(fn)

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapped(*args: Any, **kwargs: Any) -> Any:
            unknown = set(kwargs) - accepted
            if unknown:
                raise ToolError(
                    f"{fn.__name__} does not accept unknown argument(s): "
                    f"{', '.join(sorted(unknown))}. Declared arguments: "
                    f"{', '.join(sorted(accepted)) or 'none'}.")
            return await fn(*args, **kwargs)
        return awrapped  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        unknown = set(kwargs) - accepted
        if unknown:
            raise ToolError(
                f"{fn.__name__} does not accept unknown argument(s): "
                f"{', '.join(sorted(unknown))}. Declared arguments: "
                f"{', '.join(sorted(accepted)) or 'none'}.")
        return fn(*args, **kwargs)
    return wrapped  # type: ignore[return-value]


def tool(app: MCPServer, *, annotations: ToolAnnotations, name: str | None = None) -> Callable[[_F], _F]:
    """Register a tool on `app`, composed with the error ladder and the unknown-argument
    refusal - the one path every tool in this server should be registered through.

    `_refuse_unknown_arguments` wraps OUTSIDE `_errors` (see the module docstring for why), and
    both wrap the RAW function before `app.tool()` derives its schema from it - the schema
    `app.tool()` computes for the wrapped function is identical to what it would compute for
    the raw one, because `functools.wraps` preserves the signature both decorators are applied
    to (`inspect.signature` follows `__wrapped__`).
    """
    def decorator(fn: _F) -> _F:
        wrapped = _refuse_unknown_arguments(_errors(fn))
        app.tool(name=name, annotations=annotations)(wrapped)
        return wrapped
    return decorator
