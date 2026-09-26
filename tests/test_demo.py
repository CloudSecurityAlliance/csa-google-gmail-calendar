"""`demonstration_plan` - the two hard rules from the brief, pinned as tests: every tool it
names must exist, and it must send mail only to the authenticated user, never an address from
an argument. See `demo.py`'s own module docstring for the full reasoning."""
from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar.mcp._tools.demo import SELF


def _plan(flavour="full", enabled=None):
    p = policy.Policy(frozenset(enabled) if enabled is not None
                      else frozenset(policy.ALL_CAPABILITIES))
    server = create_server(backend=None, policy=p, flavour=flavour)
    return server, server._tool_manager.get_tool("demonstration_plan").fn()


def test_every_step_names_a_tool_that_actually_exists():
    server, plan = _plan()
    names = {t.name for t in server._tool_manager.list_tools()}
    for step in plan["steps"]:
        assert step["tool"] in names, step["tool"]


def test_every_step_names_a_tool_that_exists_under_a_narrower_flavour_too():
    """The same invariant, under `core` - a narrower flavour must shrink the PLAN, never leave
    a step naming a tool that flavour just removed."""
    server, plan = _plan(flavour="core")
    names = {t.name for t in server._tool_manager.list_tools()}
    for step in plan["steps"]:
        assert step["tool"] in names, step["tool"]


def test_a_tool_the_flavour_removed_is_never_planned_but_is_named_unavailable():
    server, plan = _plan(flavour="core")
    planned = {step["tool"] for step in plan["steps"]}
    assert "create_event" not in planned
    assert "create_event" in plan["unavailable"]


def test_the_demo_does_not_send_mail_to_anyone_but_the_authenticated_user():
    """A demo that emails a stranger is an incident, not a test. Every step whose OWN
    `arguments` dict carries a `to`/`cc`/`bcc` key - for every send-capable tool this server
    has, not only the three the brief's own illustrative test names - must be exactly the
    self-placeholder, and nothing else."""
    _, plan = _plan()
    checked = 0
    for step in plan["steps"]:
        args = step["arguments"]
        for key in ("to", "cc", "bcc"):
            if key in args:
                checked += 1
                assert args[key] == [SELF], (step["tool"], key, args[key])
    # A regression that silently dropped every `to`/`cc`/`bcc` argument from every step would
    # make the loop above vacuously pass - assert it actually checked something real.
    assert checked >= 4


def test_the_brief_s_own_illustrative_check_holds_for_the_three_named_tools():
    """The literal sample from the task brief, kept verbatim as its own test alongside the
    more general one above."""
    _, plan = _plan()
    for step in plan["steps"]:
        if step["tool"] in ("send_message", "reply", "reply_all"):
            if "to" in step["arguments"]:
                assert step["arguments"]["to"] == ["<the authenticated user>"]


def test_reply_and_reply_all_carry_no_to_argument_at_all():
    """`reply`/`reply_all` resolve their own recipient from the message being replied to - they
    take no `to` argument on this server, so their safety does not rest on an argument value at
    all. Documented here so a future change that ADDS a `to` argument to either tool is forced
    to reconsider this plan, rather than silently becoming exploitable through it."""
    _, plan = _plan()
    for step in plan["steps"]:
        if step["tool"] in ("reply", "reply_all"):
            assert "to" not in step["arguments"]


def test_forward_is_explicitly_self_addressed():
    """`forward` has a REQUIRED `to` argument with no implicit recipient - unlike reply/
    reply_all, its safety depends entirely on this plan supplying the self-placeholder."""
    _, plan = _plan()
    forwards = [s for s in plan["steps"] if s["tool"] == "forward"]
    assert forwards
    for step in forwards:
        assert step["arguments"]["to"] == [SELF]


def test_create_event_never_invites_anyone_but_the_authenticated_user():
    """The calendar half of the same rule: a scratch event's only attendee, if any, must be the
    authenticated user - never a stranger's address baked into the plan."""
    _, plan = _plan()
    for step in plan["steps"]:
        if step["tool"] == "create_event":
            attendees = step["arguments"].get("attendees") or []
            assert attendees == [SELF] or attendees == []


def test_demonstration_plan_does_not_include_itself():
    _, plan = _plan()
    assert "demonstration_plan" not in {s["tool"] for s in plan["steps"]}


def test_demonstration_plan_never_calls_logout_or_authenticate():
    """`logout` would end the session's own credential; `authenticate` is on-demand, not
    something a demo should force - both are documented, deliberate omissions."""
    _, plan = _plan()
    named = {s["tool"] for s in plan["steps"]}
    assert "logout" not in named
    assert "authenticate" not in named


def test_demonstration_plan_makes_no_backend_call():
    """`demonstration_plan` is called with `backend=None` in every test above - if it touched
    `backend` at all this whole file would already be failing with an AttributeError, but this
    test names the invariant explicitly rather than leaving it implicit in the fact that the
    others happen to pass."""
    server = create_server(backend=None, policy=policy.Policy())
    plan = server._tool_manager.get_tool("demonstration_plan").fn()
    assert plan["steps"]


def test_cleanup_possible_reflects_whether_calendar_delete_is_enabled():
    _, plan_without = _plan(enabled=set(policy.DEFAULT_ENABLED))
    assert plan_without["cleanup_possible"] is False
    _, plan_with = _plan(enabled=set(policy.ALL_CAPABILITIES))
    assert plan_with["cleanup_possible"] is True


def test_running_the_plan_twice_produces_two_differently_named_labels():
    """Safe to run twice: repeated `demonstration_plan` calls must not collide on the one
    artefact this server cannot delete again (a label - there is no `delete_label` tool)."""
    server = create_server(backend=None, policy=policy.Policy())
    tool = server._tool_manager.get_tool("demonstration_plan")
    first = tool.fn()
    second = tool.fn()
    label_step_1 = next(s for s in first["steps"] if s["tool"] == "create_label")
    label_step_2 = next(s for s in second["steps"] if s["tool"] == "create_label")
    assert label_step_1["arguments"]["name"] != label_step_2["arguments"]["name"]
