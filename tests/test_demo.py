"""`demonstration_plan` - the two hard rules from the brief, pinned as tests: every tool it
names must exist, and it must send mail only to the authenticated user, never an address from
an argument. See `demo.py`'s own module docstring for the full reasoning."""
from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import create_server
from csa_google_gmail_calendar.mcp._tools.demo import _NOT_DEMONSTRATED, SELF, _catalogue


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


def test_get_attachment_is_demonstrated_against_a_fixture_the_demo_itself_creates():
    """get_attachment is a real tool with a security-relevant containment check on a
    message-supplied filename - it must not be quietly left out. The demo creates its own
    fixture (send_message's own attachment) rather than hoping a real inbox happens to have
    one."""
    _, plan = _plan()
    named = {s["tool"] for s in plan["steps"]}
    assert "get_attachment" in named
    send_step = next(s for s in plan["steps"] if s["tool"] == "send_message")
    assert "attachments" in send_step["arguments"]
    attachment_step = next(s for s in plan["steps"] if s["tool"] == "get_attachment")
    assert "message_id" in attachment_step["arguments"]
    assert "attachment_id" in attachment_step["arguments"]
    assert "filename" in attachment_step["arguments"]
    # Every send-tier step in this plan targets the same self-sent message via an identical
    # placeholder string ("<the same message id from send_message above>") - assert the
    # get_attachment step follows that same convention rather than inventing its own.
    assert attachment_step["arguments"]["message_id"] == \
        "<the same message id from send_message above>"


def test_every_registered_tool_is_either_demonstrated_or_explained():
    """A hand-authored catalogue nobody checks is a catalogue that drifts silently. Every tool
    this server can EVER register (full flavour, every capability enabled) must appear as a
    step in `_catalogue` OR be named in `_NOT_DEMONSTRATED` with its own one-line reason - never
    neither, and never both."""
    server = create_server(backend=None, policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)))
    all_names = {t.name for t in server._tool_manager.list_tools()}
    catalogued = {entry.tool for entry in _catalogue("test-run-id")}
    explained = set(_NOT_DEMONSTRATED)

    unexplained = all_names - catalogued - explained
    assert not unexplained, f"neither demonstrated nor explained: {sorted(unexplained)}"

    overlap = catalogued & explained
    assert not overlap, f"both demonstrated and marked not-demonstrated: {sorted(overlap)}"

    phantom_reasons = explained - all_names
    assert not phantom_reasons, f"_NOT_DEMONSTRATED names a tool that does not exist: {sorted(phantom_reasons)}"


def test_not_demonstrated_is_reported_back_in_the_plan():
    _, plan = _plan()
    assert plan["not_demonstrated"] == _NOT_DEMONSTRATED


def test_mail_send_only_configuration_produces_no_unresolvable_send_step():
    """The scenario flagged in review: `CSA_GGC_CAPABILITIES=mail.send` registers
    send_message/forward/reply/reply_all/send_draft (all gated mail.send) but NOT whoami or
    create_draft (both gated mail.read/mail.write, neither enabled here). Every one of those
    five must therefore be DROPPED, not planned with a placeholder nothing can resolve."""
    _, plan = _plan(enabled={policy.MAIL_SEND})
    planned = {step["tool"] for step in plan["steps"]}
    for risky in ("send_message", "forward", "reply", "reply_all", "send_draft"):
        assert risky not in planned, f"{risky} was planned without whoami/create_draft available"

    skipped_tools = {entry["tool"] for entry in plan["skipped"]}
    assert {"send_message", "forward", "reply", "reply_all", "send_draft"} <= skipped_tools
    for entry in plan["skipped"]:
        assert entry["reason"]                        # every skip states why


def test_mail_send_only_configuration_still_plans_the_dependency_free_steps():
    """Dropping the unsafe steps must not silently drop everything - auth_status/
    describe_configuration/report_a_problem have no prerequisites and no capability gate."""
    _, plan = _plan(enabled={policy.MAIL_SEND})
    planned = {step["tool"] for step in plan["steps"]}
    assert {"auth_status", "describe_configuration", "report_a_problem"} <= planned


def test_a_step_whose_only_dependency_chain_leads_to_an_unavailable_tool_is_also_skipped():
    """Cascading dependency: get_attachment needs send_message (via get_message), and
    send_message needs whoami - under mail.send-only, get_attachment must be dropped too, not
    just the tool it directly names."""
    _, plan = _plan(enabled={policy.MAIL_SEND})
    planned = {step["tool"] for step in plan["steps"]}
    assert "get_attachment" not in planned


def test_mail_cleanup_possible_is_false_when_mail_write_is_not_enabled():
    """mail.send alone can create real messages (send_message/forward/reply/... are gated
    mail.send, not mail.write) but trash_email/trash_thread need mail.write - a deployment
    that can send but not clean up must say so."""
    _, plan = _plan(enabled={policy.MAIL_SEND})
    assert plan["mail_cleanup_possible"] is False


def test_mail_cleanup_possible_is_true_under_the_default_policy():
    _, plan = _plan(enabled=set(policy.DEFAULT_ENABLED))
    assert plan["mail_cleanup_possible"] is True


def test_advice_warns_when_mail_cleanup_is_not_possible():
    _, plan = _plan(enabled={policy.MAIL_SEND})
    assert "mail_cleanup_possible" in plan["advice"]


def test_final_cleanup_trashes_every_thread_this_run_created():
    """Fix: the demo used to end its self-sent thread on `untrash_thread` - i.e. RESTORED, not
    cleaned up - and never touched forward's or send_draft's own separate threads at all. Now
    there must be a trash_thread/trash_email call for each, with no untrash after it."""
    _, plan = _plan()
    trash_thread_steps = [s for s in plan["steps"] if s["tool"] == "trash_thread"]
    trash_email_steps = [s for s in plan["steps"] if s["tool"] == "trash_email"]
    # One trash_thread for the reversibility demo, one more as final cleanup.
    assert len(trash_thread_steps) == 2
    # One trash_email for the reversibility demo, plus final cleanup of forward's and
    # send_draft's own separate threads.
    assert len(trash_email_steps) == 3

    # The very last mail-tier disposal step touching the self-sent thread must be a trash,
    # never followed by an untrash - i.e. the plan does not end the story by restoring it.
    thread_related = [s["tool"] for s in plan["steps"]
                      if s["tool"] in ("trash_thread", "untrash_thread")]
    assert thread_related[-1] == "trash_thread"


def test_untrash_steps_still_appear_to_demonstrate_reversibility():
    """The reversibility demonstration is deliberately kept - only the ENDING changed."""
    _, plan = _plan()
    named = {s["tool"] for s in plan["steps"]}
    assert "untrash_email" in named
    assert "untrash_thread" in named


def test_advice_never_promises_the_label_is_cleaned_up():
    _, plan = _plan()
    assert "never deleted" in plan["advice"].lower() or "not deleted" in plan["advice"].lower()


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
