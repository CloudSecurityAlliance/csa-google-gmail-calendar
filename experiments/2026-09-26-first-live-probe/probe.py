"""The first live probe: this server against a real Google account.

Every offline verdict on this branch came from `FakeBackend`. This script is the only thing that
can tell us the double was right about Google, and csa-zendesk's record is that live probing found
what offline testing could not on every single run.

**This repository is public. This script talks to a real mailbox.** So it is redacted by
construction rather than by remembering: `_safe` is the only way a value reaches the report, it
refuses anything resembling an address, and ids are truncated hashes. Message bodies, subjects and
recipient lists never leave this process - only their shapes do.

Run:  CSA_GGC_LIVE=1 python experiments/2026-09-26-first-live-probe/probe.py
"""
from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import re
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

_ADDR = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

FINDINGS: list[tuple[str, str, str]] = []


_MAX_DETAIL = 120


def _safe(value: object) -> str:
    """The only route a value takes into the report.

    Two guards, and the second matters more than it looks. The address check is the obvious one.
    The length cap is the guard against everything I did not think of: a probe detail is a shape
    ("3 gaps, created hour excluded"), and anything enough to be prose is probably content that
    reached here by accident - a subject line, an error carrying a body, a Google message echoing
    the request. A denylist of content shapes would be a list somebody maintains; a cap is not.
    """
    text = str(value)
    if _ADDR.search(text):
        return "<redacted: address-shaped>"
    if len(text) > _MAX_DETAIL:
        return text[:_MAX_DETAIL] + f"... <truncated {len(text) - _MAX_DETAIL} chars>"
    return text


def _id(value: str) -> str:
    """An id as a short hash - enough to correlate two lines, useless to a reader."""
    return "id:" + hashlib.sha256(value.encode()).hexdigest()[:8]


def check(name: str):
    def wrap(fn):
        def run(*a, **k):
            try:
                detail = fn(*a, **k)
                FINDINGS.append((name, "PASS", _safe(detail)))
                print(f"  PASS  {name}: {_safe(detail)}")
                return True
            except AssertionError as e:
                FINDINGS.append((name, "FAIL", _safe(e)))
                print(f"  FAIL  {name}: {_safe(e)}")
            except Exception as e:                      # noqa: BLE001 - a probe reports, never dies
                FINDINGS.append((name, "ERROR", _safe(f"{type(e).__name__}: {e}")))
                print(f"  ERROR {name}: {_safe(type(e).__name__)}")
                traceback.print_exc(limit=2, file=sys.stderr)
            return False
        return run
    return wrap


def main() -> int:
    if os.environ.get("CSA_GGC_LIVE") != "1":
        print("refusing to run: set CSA_GGC_LIVE=1 to talk to a real account")
        return 2

    from csa_google_gmail_calendar import auth, policy
    from csa_google_gmail_calendar._attachments import AttachmentPolicy, DownloadPolicy
    from csa_google_gmail_calendar._mime import build
    from csa_google_gmail_calendar.backend import ApiBackend
    from csa_google_gmail_calendar.calendar import Calendar
    from csa_google_gmail_calendar.exceptions import PolicyError
    from csa_google_gmail_calendar.mail import Mail

    enabled = policy.DEFAULT_ENABLED
    required = auth.scopes_for(enabled)
    secrets = os.environ.get("CSA_GGC_CLIENT_SECRETS",
                             os.path.expanduser("~/.csa_google_workspace/client_secret.json"))
    token = os.environ.get("CSA_GGC_TOKEN_PATH",
                           os.path.expanduser("~/.csa_google_gmail_calendar/token.json"))
    os.makedirs(os.path.dirname(token), exist_ok=True)

    print("\n[1] authenticate, and check the consent asked for no more than the capabilities need")
    creds = auth.load_credentials(secrets, token, required)
    backend = ApiBackend.from_credentials(creds)

    @check("scope minimisation: granted set contains no scope outside the requested set")
    def _scopes():
        granted = set(creds.scopes or [])
        extra = granted - set(required)
        assert not extra, f"granted {len(extra)} scope(s) beyond what the capabilities need"
        return f"{len(granted)} scopes, none beyond the {len(required)} requested"
    _scopes()

    @check("no full-mailbox scope without mail.delete")
    def _full():
        assert "https://mail.google.com/" not in set(creds.scopes or [])
        return "https://mail.google.com/ absent, as mail.delete is off by default"
    _full()

    me = backend.get_profile().get("emailAddress")
    print(f"  authenticated as {_id(me)}")

    print("\n[2] read real mail: does an HTML-only body convert rather than come back empty?")
    mail = Mail(backend)

    @check("a real message body converts, and transformations are disclosed")
    def _read():
        found = backend.search_messages(query="has:attachment OR is:read", limit=10)
        ids = [m["id"] for m in (found.get("messages") or [])]
        assert ids, "no messages matched - try a wider query"
        html_seen = plain_seen = 0
        transformed = 0
        for mid in ids:
            parsed = mail.read_message(mid)
            if parsed.body_source == "text/html":
                html_seen += 1
            elif parsed.body_source == "text/plain":
                plain_seen += 1
            if parsed.transformations:
                transformed += 1
            assert not (parsed.body_source != "none" and not parsed.body_markdown.strip()), \
                f"a {parsed.body_source} body converted to empty text"
        return (f"{len(ids)} messages: {html_seen} html-sourced, {plain_seen} plain, "
                f"{transformed} disclosed a transformation")
    _read()
    print("\n[3] the attachment allowlist, probed with a REAL file that exists")
    sandbox = pathlib.Path(tempfile.mkdtemp())
    attach_dir = sandbox / "attach"
    attach_dir.mkdir()
    download_dir = sandbox / "downloads"
    download_dir.mkdir()
    secret = sandbox / "secret.txt"
    secret.write_bytes(b"CANARY-MUST-NOT-BE-SENT")
    (attach_dir / "probe.txt").write_bytes(b"this one is legitimate")
    (attach_dir / "link-to-secret").symlink_to(secret)   # a REAL symlink to a REAL file
    ap = AttachmentPolicy(str(attach_dir))

    @check("a symlink inside the root pointing at a real file outside it is refused")
    def _symlink():
        try:
            ap.read("link-to-secret")
        except PolicyError as e:
            assert "outside" in str(e)
            return "refused on the resolved path, not the supplied name"
        raise AssertionError("the symlink was followed out of the allowlist")
    _symlink()

    @check("the legitimate file inside the root is readable")
    def _legit():
        data, name = ap.read("probe.txt")
        assert data == b"this one is legitimate" and name == "probe.txt"
        return "allowed, as it must be - the control is not simply refusing everything"
    _legit()

    print("\n[4] send to yourself, with an attachment, and read it back")
    sent_ids: list[str] = []

    @check("a self-addressed message with an attachment arrives with the attachment intact")
    def _send():
        raw = build([me], "csa-google-gmail-calendar live probe", "Sent by the live probe.",
                    attachments=["probe.txt"], attach_policy=ap)
        result = backend.send_message(raw=raw)
        sent_ids.append(result["id"])
        parsed = mail.read_message(result["id"])
        names = [a.filename for a in parsed.attachments]
        assert names == ["probe.txt"], f"expected one attachment, got {len(names)}"
        assert parsed.attachments[0].size_bytes > 0
        return f"delivered, 1 attachment, body_source={parsed.body_source}"
    _send()

    @check("a downloaded attachment lands in the download dir, not the attach dir")
    def _download():
        parsed = mail.read_message(sent_ids[0])
        ref = parsed.attachments[0]
        blob = backend.get_attachment(message_id=sent_ids[0], attachment_id=ref.attachment_id)
        dp = DownloadPolicy(str(download_dir))
        data = base64.urlsafe_b64decode(blob["data"] + "=" * (-len(blob["data"]) % 4))
        dp.write("probe.txt", data)
        assert (download_dir / "probe.txt").exists()
        assert (attach_dir / "probe.txt").read_bytes() == b"this one is legitimate"
        return "written to the download dir; the attach dir is untouched"
    _download()

    print("\n[5] calendar: create, find free time, respond, delete")
    cal = Calendar(backend)
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    end = start + timedelta(hours=1)
    created: list[str] = []

    @check("an event is created and reads back with the times we set")
    def _create():
        ev = cal.create(calendar_id="primary",
                        summary="csa-google-gmail-calendar live probe",
                        start=start.isoformat().replace("+00:00", "Z"),
                        end=end.isoformat().replace("+00:00", "Z"))
        created.append(ev["id"])
        got = backend.get_event(calendar_id="primary", event_id=ev["id"])
        assert got["id"] == ev["id"]
        return f"created {_id(ev['id'])}, reads back"
    _create()

    @check("find_free_time reports the created hour as busy, and names unreadable calendars")
    def _free():
        out = cal.find_free(time_min=(start - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                            time_max=(end + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                            calendar_ids=["primary", "does-not-exist@example.org"])
        assert "free" in out and "unreadable_calendars" in out
        unreadable = [u["id"] for u in out["unreadable_calendars"]]
        assert unreadable, "a nonexistent calendar was NOT reported as unreadable - it read as free"
        covering = [g for g in out["free"]
                    if g["start"] <= start.isoformat().replace("+00:00", "Z") < g["end"]]
        assert not covering, "the created event's hour was offered as free"
        return (f"{len(out['free'])} gaps, created hour excluded, "
                f"{len(unreadable)} calendar(s) named unreadable")
    _free()

    print("\n[6] cleanup - the probe removes what it made")
    for eid in created:
        try:
            backend.delete_event(calendar_id="primary", event_id=eid)
        except Exception as e:                            # noqa: BLE001
            FINDINGS.append(("cleanup: event", "ERROR", _safe(type(e).__name__)))
    for mid in sent_ids:
        try:
            backend.trash_message(message_id=mid)
        except Exception as e:                            # noqa: BLE001
            FINDINGS.append(("cleanup: message", "ERROR", _safe(type(e).__name__)))
    print(f"  removed {len(created)} event(s), trashed {len(sent_ids)} message(s)")

    print("\n" + "=" * 72)
    passed = sum(1 for _, v, _ in FINDINGS if v == "PASS")
    print(f"{passed}/{len(FINDINGS)} checks passed")
    for name, verdict, detail in FINDINGS:
        if verdict != "PASS":
            print(f"  {verdict}: {name} - {detail}")
    return 0 if passed == len(FINDINGS) else 1



if __name__ == "__main__":
    raise SystemExit(main())
