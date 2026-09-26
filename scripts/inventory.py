#!/usr/bin/env python3
"""Enumerate every method in the snapshotted Google Discovery documents.

Emits analysis/operation-inventory.csv (one row per method) and prints a
per-API summary. Re-run after refreshing specs/ to see upstream drift.

Google publishes DISCOVERY documents, not OpenAPI. The shape differs in three
ways that matter here:

  * Methods hang off a RECURSIVE `resources` tree, not a flat `paths` map. Gmail
    has exactly one top-level resource (`users`) and buries everything under it,
    so a non-recursive reader reports 0 operations for Gmail and is not obviously
    wrong until you look at the number.
  * Each method carries its own `scopes` list. That is the authorization fact the
    policy layer binds to, and it is per-method rather than per-tag.
  * There is no `deprecated` flag. Deprecation is prose in `description` only, so
    it is detected by string match and will drift.
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys
from collections import Counter, defaultdict

# Import path: this script is invoked as `python scripts/inventory.py`, not through the
# installed package's console entry point, so it needs src/ on sys.path even when the package
# is also pip-installed editable (which it is, in dev).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from csa_google_gmail_calendar.scopes import narrowest  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS = {
    "gmail": ROOT / "specs/gmail-v1-discovery.json",
    "calendar": ROOT / "specs/calendar-v3-discovery.json",
}
# A method is mutating if its HTTP verb changes state. Discovery gives us the verb
# honestly, so this needs no per-method table.
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
SCOPE_PREFIX = "https://www.googleapis.com/auth/"


def walk(resources: dict, trail: tuple[str, ...] = ()):
    """Yield (family, method_name, method) over the recursive resource tree."""
    for name, node in (resources or {}).items():
        here = (*trail, name)
        for method_name, method in (node.get("methods") or {}).items():
            yield ".".join(here), method_name, method
        yield from walk(node.get("resources") or {}, here)


def _narrowest_or_blank(api: str, scopes: list[str]) -> str:
    """Blank rather than a guess when the lattice cannot rank anything (issue #11)."""
    if not scopes:
        return ""
    try:
        return narrowest(api, scopes)
    except ValueError:
        return ""


def paging_of(method: dict) -> str:
    """Which pagination the method DECLARES. Google is consistent here in a way
    Zendesk was not: a `pageToken` parameter means cursor paging, full stop."""
    params = method.get("parameters") or {}
    if "pageToken" in params:
        return "cursor"
    if "syncToken" in params:
        return "sync"
    return ""


def main() -> int:
    rows = []
    meta = {}
    for api, path in SPECS.items():
        if not path.exists():
            print(f"missing spec: {path}", file=sys.stderr)
            return 1
        doc = json.loads(path.read_text())
        meta[api] = (doc.get("id"), doc.get("revision"))
        for family, method_name, m in walk(doc.get("resources") or {}):
            # RANK/UNRANKED in scopes.py key on the full scope URL (including
            # "https://mail.google.com/", which SCOPE_PREFIX does not match), so narrowest()
            # must see the unstripped list. `scopes` (stripped) stays the display column.
            scopes_full = list(m.get("scopes") or [])
            scopes = [s.removeprefix(SCOPE_PREFIX) for s in scopes_full]
            desc = (m.get("description") or "").strip().replace("\n", " ")
            rows.append({
                "api": api,
                "family": family,
                "method": method_name,
                "http": m.get("httpMethod", ""),
                "path": m.get("path", ""),
                "operation_id": m.get("id", ""),
                "mutating": "yes" if m.get("httpMethod") in MUTATING else "",
                "paging": paging_of(m),
                # Prose-only. Discovery has no deprecated flag; this WILL drift.
                "deprecated": "yes" if "deprecat" in desc.lower() else "",
                "media_download": "yes" if m.get("supportsMediaDownload") else "",
                "media_upload": "yes" if m.get("mediaUpload") else "",
                "scopes": " ".join(scopes),
                "narrowest_scope": _narrowest_or_blank(api, scopes_full),
                "summary": desc[:300],
            })

    rows.sort(key=lambda r: (r["api"], r["family"], r["method"]))
    out = ROOT / "analysis/operation-inventory.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} operations -> {out.relative_to(ROOT)}\n")
    by_api: dict[str, list] = defaultdict(list)
    for r in rows:
        by_api[r["api"]].append(r)
    for api, rs in by_api.items():
        ident, rev = meta[api]
        verbs = Counter(r["http"] for r in rs)
        print(f"{api:9s} {ident} rev {rev}")
        print(f"{'':9s} {len(rs):4d} methods  {len({r['family'] for r in rs}):3d} families  "
              f"{dict(verbs)}")
        print(f"{'':9s} mutating: {sum(1 for r in rs if r['mutating'])}  "
              f"paged: {sum(1 for r in rs if r['paging'])}  "
              f"scopes in use: {len({s for r in rs for s in r['scopes'].split() if s})}")
        fam = Counter(r["family"] for r in rs)
        print(f"{'':9s} families: " + ", ".join(f"{k}({v})" for k, v in sorted(fam.items())))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
