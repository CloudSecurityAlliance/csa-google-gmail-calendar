import csv
import pathlib


def test_no_calendar_row_claims_bare_calendar_is_narrowest():
    """Regression for #11. Bare `calendar` is full access; it is never the narrowest."""
    rows = list(csv.DictReader(open(pathlib.Path("analysis/operation-inventory.csv"))))
    cal = [r for r in rows if r["api"] == "calendar" and r["narrowest_scope"]]
    assert cal, "calendar rows present"
    bad = [r for r in cal if r["narrowest_scope"].endswith("/auth/calendar")]
    assert not bad, f"{len(bad)} calendar rows still name the broadest scope as narrowest"

def test_gmail_reads_do_not_name_a_write_scope():
    rows = list(csv.DictReader(open(pathlib.Path("analysis/operation-inventory.csv"))))
    reads = [r for r in rows
             if r["api"] == "gmail" and r["mutating"].lower() in ("", "false", "no", "0")
             and r["narrowest_scope"]]
    assert reads
    bad = [r for r in reads if r["narrowest_scope"].endswith(("gmail.modify", "mail.google.com/"))]
    assert not bad, f"{len(bad)} gmail reads name a write scope as narrowest"
