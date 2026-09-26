import pytest

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.exceptions import PolicyError


def test_default_posture_enables_reads_and_reversible_writes():
    assert policy.MAIL_READ in policy.DEFAULT_ENABLED
    assert policy.MAIL_WRITE in policy.DEFAULT_ENABLED
    assert policy.MAIL_SEND in policy.DEFAULT_ENABLED
    assert policy.CALENDAR_READ in policy.DEFAULT_ENABLED
    assert policy.CALENDAR_WRITE in policy.DEFAULT_ENABLED


def test_default_posture_disables_what_persists_or_destroys():
    assert policy.MAIL_DELETE not in policy.DEFAULT_ENABLED
    assert policy.CALENDAR_DELETE not in policy.DEFAULT_ENABLED


def test_every_backend_method_has_a_gate():
    """Fail-closed: a method with no gate is a method nobody decided about."""
    from csa_google_gmail_calendar.backend import Backend
    methods = {m for m in dir(Backend) if not m.startswith("_")}
    assert methods <= set(policy._GATES), f"ungated: {sorted(methods - set(policy._GATES))}"


def test_require_refuses_a_disabled_capability_by_name():
    p = policy.Policy(frozenset({policy.MAIL_READ}))
    with pytest.raises(PolicyError, match="mail.send"):
        p.require("send_message")


def test_refusal_names_the_env_var_that_would_enable_it():
    """DEC-020: a refusal is a negotiation. It says what would change the answer."""
    p = policy.Policy(frozenset({policy.MAIL_READ}))
    with pytest.raises(PolicyError, match="CSA_GGC_CAPABILITIES"):
        p.require("send_message")


def test_refusal_for_an_off_by_default_capability_says_off_by_default():
    """CALENDAR_DELETE ships off in DEFAULT_ENABLED, so a policy that never enabled it (the
    common case - nobody had to narrow anything) should be told it's off by default."""
    p = policy.Policy(frozenset({policy.CALENDAR_READ}))
    with pytest.raises(PolicyError, match="off by default"):
        p.require("delete_event")


def test_refusal_for_a_narrowed_on_by_default_capability_does_not_say_off_by_default():
    """Fix round 1: MAIL_SEND is in both IRREVERSIBLE and DEFAULT_ENABLED. A policy that
    narrowed it away must not be told "off by default" - that's false, and it's this
    deployment's own configuration that refused it, not the shipped default."""
    p = policy.Policy(frozenset({policy.MAIL_READ}))
    with pytest.raises(PolicyError) as excinfo:
        p.require("send_message")
    message = str(excinfo.value)
    assert "off by default" not in message
    assert "this deployment's own configuration" in message


def test_policy_backend_refuses_before_the_inner_backend_is_touched():
    calls = []

    class Spy:
        def send_message(self, **kw):
            calls.append(kw)
            return {}

    pb = policy.PolicyBackend(Spy(), policy.Policy(frozenset({policy.MAIL_READ})))
    with pytest.raises(PolicyError):
        pb.send_message(to=["a@example.com"], subject="x", body="y")
    assert calls == [], "the gate must fire before the call, not after"


def test_delete_draft_is_gated_mail_write_not_mail_delete():
    """Deviation from the brief (CINO, 2026-09-25): deleting an unsent draft changes nobody's
    access and persists nothing, so spec §3's posture rule puts it in MAIL_WRITE, not
    MAIL_DELETE - see the comment on this line in policy.py for the full reasoning."""
    assert policy._GATES["delete_draft"].capability == policy.MAIL_WRITE


def test_mail_delete_gates_nothing_yet():
    """MAIL_DELETE is reserved for the 3 permanent-destroy Gmail methods (messages.delete,
    messages.batchDelete, threads.delete) - none of which any Backend method in this plan
    implements yet, so no gate should reference it."""
    gated_capabilities = {gate.capability for gate in policy._GATES.values()}
    assert policy.MAIL_DELETE not in gated_capabilities


def test_policy_backend_forwards_a_non_callable_attribute():
    """A Backend attribute that isn't a method (e.g. a plain property) passes through
    ungated - only callables get wrapped."""

    class Spy:
        some_value = 42

    pb = policy.PolicyBackend(Spy(), policy.Policy())
    assert pb.some_value == 42


def test_policy_backend_getattr_fails_closed_for_an_ungated_method():
    """A Backend method with no entry in _GATES must be refused, not allowed through."""

    class Spy:
        def not_a_real_backend_method(self):
            return "should never run"

    pb = policy.PolicyBackend(Spy(), policy.Policy())
    with pytest.raises(PolicyError):
        pb.not_a_real_backend_method()


def test_policy_backend_inner_is_reachable_and_unpoliced():
    """Documents the boundary, rather than hiding it (ruling, fix round 1): `pb._inner` is a
    plain instance attribute, resolved by normal `__getattribute__` before `__getattr__` (the
    gate) is ever consulted. Calling through it bypasses the policy entirely - intended, since
    `PolicyBackend` is a seam on the call path, not a sandbox around the object. Anyone holding
    a Python reference to `pb` could equally have constructed an unpoliced backend directly,
    so this is not a gap introduced by this class; it is what "a seam, not a sandbox" means."""
    calls = []

    class Spy:
        def send_message(self, **kw):
            calls.append(kw)
            return {"sent": True}

    spy = Spy()
    pb = policy.PolicyBackend(spy, policy.Policy(frozenset({policy.MAIL_READ})))

    assert pb._inner is spy

    with pytest.raises(PolicyError):
        pb.send_message(to=["a@example.com"], subject="x", body="y")
    assert calls == []

    result = pb._inner.send_message(to=["a@example.com"], subject="x", body="y")
    assert result == {"sent": True}
    assert calls == [{"to": ["a@example.com"], "subject": "x", "body": "y"}]


# --- Fix round 3, item 3: reads are gated by capability, not unconditionally allowed ---------
#
# This is the riskiest edit in fix round 2 (gating every read behind MAIL_READ/CALENDAR_READ
# instead of `Gate(None)`), and it shipped with no test asserting the new refusal - only an
# argument that nothing EXISTING broke, which is not the same claim. These four close that gap:
# one refusal each for mail and calendar, and - just as important - the two assertions that the
# default posture is UNCHANGED, since those are what would catch someone over-tightening later.

def test_a_mail_read_is_refused_when_mail_read_is_not_enabled():
    p = policy.Policy(frozenset({policy.MAIL_SEND}))
    with pytest.raises(PolicyError, match="mail.read"):
        p.require("get_message")


def test_a_calendar_read_is_refused_when_calendar_read_is_not_enabled():
    p = policy.Policy(frozenset({policy.MAIL_READ}))
    with pytest.raises(PolicyError, match="calendar.read"):
        p.require("get_event")


def test_policy_backend_refuses_a_read_before_delegating_when_its_capability_is_narrowed_away():
    calls = []

    class Spy:
        def search_messages(self, **kw):
            calls.append(kw)
            return {"messages": []}

    pb = policy.PolicyBackend(Spy(), policy.Policy(frozenset({policy.MAIL_SEND})))
    with pytest.raises(PolicyError):
        pb.search_messages(query="x")
    assert calls == [], "the gate must fire before the call, not after"


def test_default_posture_still_permits_every_read():
    """The assertion that would catch someone over-tightening _GATES later, not just the one
    that catches the narrowing this fix round added: every method gated MAIL_READ or
    CALENDAR_READ must still be allowed under DEFAULT_ENABLED, since both capabilities ship on."""
    p = policy.Policy()  # DEFAULT_ENABLED
    read_methods = [m for m, gate in policy._GATES.items()
                    if gate.capability in (policy.MAIL_READ, policy.CALENDAR_READ)]
    assert read_methods, "sanity: there should be read methods to check"
    for method in read_methods:
        assert p.allows(method), f"{method} should be allowed under the default posture"


def test_default_posture_permits_a_read_through_policy_backend():
    calls = []

    class Spy:
        def get_message(self, **kw):
            calls.append(kw)
            return {"id": "m1"}

    pb = policy.PolicyBackend(Spy(), policy.Policy())  # DEFAULT_ENABLED
    result = pb.get_message(message_id="m1")
    assert result == {"id": "m1"}
    assert calls == [{"message_id": "m1"}]
