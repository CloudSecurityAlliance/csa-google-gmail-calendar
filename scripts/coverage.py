#!/usr/bin/env python3
"""Map every API method onto the MCP tools that reach it, for each surface.

Emits analysis/coverage-matrix.csv (one row per method) and, with --markdown, the
coverage tables spliced into README.md between the COVERAGE markers.

Two surfaces are compared:
  google     https://gmailmcp.googleapis.com/mcp/v1 + calendarmcp - 23 + 9 tools
  connector  the claude.ai Gmail / Google Calendar connectors     - 29 + 9 tools

They are one Google implementation at two exposure levels, not two products - all
32 shared tools are schema-identical. See analysis/observed-mcp-tools.json.

TOOL_MAP is hand-built and is the only hand-built thing here. A tool is not an
endpoint: Google expands users.messages.modify into seven tools (label/unlabel/
update_labels x message/thread, plus the spam pair) and collapses nothing. So the
mapping is many-to-many in both directions and cannot be derived from either side.
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent

# tool -> the API method ids it reaches. Empty list = no REST equivalent.
TOOL_MAP: dict[str, list[str]] = {
    # --- Gmail, on both surfaces -------------------------------------------------
    "search_threads":       ["gmail.users.threads.list"],
    "get_thread":           ["gmail.users.threads.get"],
    "get_message":          ["gmail.users.messages.get"],
    "get_draft":            ["gmail.users.drafts.get"],
    "list_drafts":          ["gmail.users.drafts.list"],
    "create_draft":         ["gmail.users.drafts.create"],
    "list_labels":          ["gmail.users.labels.list"],
    "create_label":         ["gmail.users.labels.create"],
    "label_message":        ["gmail.users.messages.modify"],
    "unlabel_message":      ["gmail.users.messages.modify"],
    "update_message_labels":["gmail.users.messages.modify"],
    "label_thread":         ["gmail.users.threads.modify"],
    "unlabel_thread":       ["gmail.users.threads.modify"],
    "trash_message":        ["gmail.users.messages.trash"],
    "untrash_message":      ["gmail.users.messages.untrash"],
    "trash_thread":         ["gmail.users.threads.trash"],
    "untrash_thread":       ["gmail.users.threads.untrash"],
    "mark_message_spam":    ["gmail.users.messages.modify"],
    "unmark_message_spam":  ["gmail.users.messages.modify"],
    "mark_thread_spam":     ["gmail.users.threads.modify"],
    "unmark_thread_spam":   ["gmail.users.threads.modify"],
    "apply_sensitive_message_label": ["gmail.users.messages.trash", "gmail.users.messages.modify"],
    "apply_sensitive_thread_label":  ["gmail.users.threads.trash",  "gmail.users.threads.modify"],
    # --- Gmail, connector only (Google authors them, withholds them publicly) -----
    "update_draft":         ["gmail.users.drafts.update"],
    "send_message":         ["gmail.users.messages.send", "gmail.users.drafts.send"],
    "reply":                ["gmail.users.messages.send"],
    "forward":              ["gmail.users.messages.send"],
    "update_label":         ["gmail.users.labels.patch"],
    "delete_label":         ["gmail.users.labels.delete"],
    # --- Calendar, identical on both surfaces ------------------------------------
    "list_calendars":       ["calendar.calendarList.list"],
    "list_events":          ["calendar.events.list"],
    "get_event":            ["calendar.events.get"],
    "create_event":         ["calendar.events.insert"],
    "update_event":         ["calendar.events.patch"],
    "delete_event":         ["calendar.events.delete"],
    "respond_to_event":     ["calendar.events.patch"],
    "suggest_time":         ["calendar.freebusy.query"],
    # Semantic search. No REST method does this; it cannot be reimplemented.
    "search_events":        [],
}

def google_tools() -> dict[str, set[str]]:
    """{"gmail": {...}, "calendar": {...}} - keyed by which endpoint published the tool,
    so the per-API counts do not depend on guessing from the method mapping."""
    out = {}
    for api, h in (("gmail", "gmailmcp"), ("calendar", "calendarmcp")):
        p = ROOT / f"research/captures/2026-09-01-{h}-tools-list.json"
        out[api] = {t["name"] for t in json.loads(p.read_text())["result"]["tools"]}
    return out

def connector_tools() -> dict[str, set[str]]:
    d = json.loads((ROOT / "analysis/observed-mcp-tools.json").read_text())
    return {api: {t["name"] for t in d["servers"][f"connector_{api}"]["tools"]}
            for api in ("gmail", "calendar")}

# How the tools group by what they do. Google expands rather than collapses, so a
# group is the unit a reader actually reasons about - "can it send?" - where a tool
# name is not. Each group names either the TOOLS that serve it or the API FAMILIES
# it covers; coverage is computed the same way for both, so a family-based group
# that happens to contain a reached method reports it instead of claiming "none".
GROUPS: list[tuple[str, list[str], list[str]]] = [
    # (label, tools, families)
    ("Read mail",           ["search_threads", "get_thread", "get_message"], []),
    ("Attachments",         [], ["users.messages.attachments"]),
    ("Drafts",              ["list_drafts", "get_draft", "create_draft", "update_draft"], []),
    ("Send",                ["send_message", "reply", "forward"], []),
    ("Label definitions",   ["list_labels", "create_label", "update_label", "delete_label"], []),
    ("Label application",   ["label_message", "unlabel_message", "update_message_labels",
                             "label_thread", "unlabel_thread"], []),
    ("Trash and spam",      ["trash_message", "untrash_message", "trash_thread", "untrash_thread",
                             "mark_message_spam", "unmark_message_spam", "mark_thread_spam",
                             "unmark_thread_spam", "apply_sensitive_message_label",
                             "apply_sensitive_thread_label"], []),
    ("Permanent delete",    [], []),          # methods listed explicitly below
    ("Incremental sync",    [], ["users.history", "channels"]),
    ("Mailbox settings",    [], ["users.settings", "users.settings.cse.identities",
                                 "users.settings.cse.keypairs", "users.settings.delegates",
                                 "users.settings.filters", "users.settings.forwardingAddresses",
                                 "users.settings.sendAs", "users.settings.sendAs.smimeInfo"]),
    ("Calendar read",       ["list_calendars", "list_events", "get_event", "search_events",
                             "suggest_time"], []),
    ("Calendar write",      ["create_event", "update_event", "delete_event", "respond_to_event"], []),
    ("Calendar list & metadata", [], ["calendarList", "colors", "settings"]),
    ("Calendar management", [], ["calendars"]),
    ("Calendar ACL",        [], ["acl"]),
]
# Permanent delete has no family of its own - the methods live inside families that
# ARE partly covered, so they must be named one by one.
PERMANENT_DELETE = ["gmail.users.messages.delete", "gmail.users.messages.batchDelete",
                    "gmail.users.threads.delete", "gmail.users.drafts.delete"]

def main() -> int:
    rows = list(csv.DictReader(open(ROOT / "analysis/operation-inventory.csv")))
    gmap, cmap = google_tools(), connector_tools()
    g = gmap["gmail"] | gmap["calendar"]
    c = cmap["gmail"] | cmap["calendar"]
    unknown = set(TOOL_MAP) - (g | c)
    if unknown:
        print(f"TOOL_MAP names no surface exposes: {sorted(unknown)}", file=sys.stderr)
        return 1
    missing = (g | c) - set(TOOL_MAP)
    if missing:
        print(f"tools with no TOOL_MAP entry: {sorted(missing)}", file=sys.stderr)
        return 1

    by_method: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"google": [], "connector": []})
    for tool, methods in TOOL_MAP.items():
        for m in methods:
            if tool in g: by_method[m]["google"].append(tool)
            if tool in c: by_method[m]["connector"].append(tool)

    out = ROOT / "analysis/coverage-matrix.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["api", "family", "operation_id", "http", "mutating",
                                           "google_tools", "connector_tools", "covered_by"])
        w.writeheader()
        for r in rows:
            hit = by_method.get(r["operation_id"], {"google": [], "connector": []})
            covered = ("both" if hit["google"] and hit["connector"]
                       else "connector-only" if hit["connector"]
                       else "google-only" if hit["google"] else "neither")
            w.writerow({"api": r["api"], "family": r["family"], "operation_id": r["operation_id"],
                        "http": r["http"], "mutating": r["mutating"],
                        "google_tools": " ".join(sorted(set(hit["google"]))),
                        "connector_tools": " ".join(sorted(set(hit["connector"]))),
                        "covered_by": covered})
    print(f"{len(rows)} methods -> {out.relative_to(ROOT)}", file=sys.stderr)

    if "--markdown" not in sys.argv:
        return 0

    L = []
    ng = sum(1 for r in rows if by_method.get(r["operation_id"], {}).get("google"))
    nc = sum(1 for r in rows if by_method.get(r["operation_id"], {}).get("connector"))
    L += [f"**{len(rows)} API methods. Google's servers reach {ng}. The claude.ai connectors reach "
          f"{nc}. Neither reaches {len(rows) - nc}.**", "",
          "| Surface | Gmail tools | Calendar tools | API methods reached |",
          "|---|---:|---:|---:|",
          f"| Google MCP (`gmailmcp`/`calendarmcp`) | {len(gmap['gmail'])} | {len(gmap['calendar'])} | {ng} of {len(rows)} |",
          f"| claude.ai connectors | {len(cmap['gmail'])} | {len(cmap['calendar'])} | {nc} of {len(rows)} |",
          f"| `csa-google-gmail-calendar` (target) | — | — | {len(rows)} of {len(rows)} |", ""]

    for api, title in (("gmail", "Gmail v1"), ("calendar", "Calendar v3")):
        rs = [r for r in rows if r["api"] == api]
        L += [f"### {title}", "",
              "| Family | Methods | Mutating | Google MCP | Anthropic connector | What is missing |",
              "|---|---:|---:|---|---|---|"]
        fams: dict[str, list] = defaultdict(list)
        for r in rs: fams[r["family"]].append(r)
        for fam in sorted(fams):
            f = fams[fam]
            gh = sum(1 for r in f if by_method.get(r["operation_id"], {}).get("google"))
            ch = sum(1 for r in f if by_method.get(r["operation_id"], {}).get("connector"))
            gaps = [r["operation_id"].split(".")[-1] for r in f
                    if not by_method.get(r["operation_id"], {}).get("connector")]
            gap = "—" if not gaps else ("**everything**" if len(gaps) == len(f)
                                        else ", ".join(f"`{x}`" for x in gaps[:4])
                                             + (f" +{len(gaps)-4}" if len(gaps) > 4 else ""))
            fmt = lambda n: "**none**" if n == 0 else f"{n}/{len(f)}"
            L.append(f"| `{fam}` | {len(f)} | {sum(1 for r in f if r['mutating'])} | "
                     f"{fmt(gh)} | {fmt(ch)} | {gap} |")
        tg = sum(1 for r in rs if by_method.get(r["operation_id"], {}).get("google"))
        tc = sum(1 for r in rs if by_method.get(r["operation_id"], {}).get("connector"))
        L += [f"| **total** | **{len(rs)}** | **{sum(1 for r in rs if r['mutating'])}** | "
              f"**{tg}/{len(rs)}** | **{tc}/{len(rs)}** | **{len(rs)-tc} methods** |", ""]

    L += ["### Grouped by what it does", "",
          "| Capability group | API methods | Google MCP | Anthropic connector |",
          "|---|---:|---|---|"]
    for label, tools, families in GROUPS:
        if tools:
            methods = {m for t_ in tools for m in TOOL_MAP.get(t_, [])}
            gi = [t_ for t_ in tools if t_ in g]
            ci = [t_ for t_ in tools if t_ in c]
            def cell(have, total_tools):
                if not have: return "**none**"
                return "all" if len(have) == len(total_tools) else f"{len(have)}/{len(total_tools)} tools"
            gcell, ccell = cell(gi, tools), cell(ci, tools)
            missing = sorted(set(ci) - set(gi))
            if missing:
                gcell += " — no " + ", ".join(f"`{x}`" for x in missing)
            n = len(methods)
        else:
            methods = (set(PERMANENT_DELETE) if label == "Permanent delete"
                       else {r["operation_id"] for r in rows if r["family"] in families})
            n = len(methods)
            gh = sum(1 for m in methods if by_method.get(m, {}).get("google"))
            ch = sum(1 for m in methods if by_method.get(m, {}).get("connector"))
            gcell = "**none**" if gh == 0 else f"{gh}/{n} methods"
            ccell = "**none**" if ch == 0 else f"{ch}/{n} methods"
        L.append(f"| {label} | {n} | {gcell} | {ccell} |")
    L.append("")
    md = "\n".join(L)
    readme = ROOT / "README.md"; t = readme.read_text()
    s, e = "<!-- COVERAGE:START -->", "<!-- COVERAGE:END -->"
    if s in t and e in t:
        t = t[:t.index(s)+len(s)] + "\n" + md + "\n" + t[t.index(e):]
        readme.write_text(t); print("README coverage tables regenerated", file=sys.stderr)
    else:
        print(md)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
