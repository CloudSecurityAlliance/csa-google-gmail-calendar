#!/usr/bin/env python3
"""Generate the README's tool table from the live tool registry, never by hand.

Answers task 13's own question ("can the README's tool table be generated from the registry
rather than hand-maintained?") with yes, on the same splice convention `coverage.py` already
uses for the coverage tables (`<!-- COVERAGE:START/END -->`) - here between
`<!-- TOOLS:START -->` / `<!-- TOOLS:END -->`. `tests/test_docs_drift.py`'s
`test_readme_lists_every_tool_the_server_registers` still means something once this script is
what fills the table: it stops proving "a human remembered to update the table" and starts
proving "somebody ran this script on the current tree" - a real, different check, not a
tautology, because CI runs the test but does not run this script; a tool added without a
regeneration still fails the drift test exactly as it would against a hand-written table.

Run: `python3 scripts/generate_tool_table.py` (regenerates README.md in place).
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from csa_google_gmail_calendar import policy  # noqa: E402
from csa_google_gmail_calendar.mcp import _capabilities, _flavours, create_server  # noqa: E402


def _first_line(description: str | None) -> str:
    if not description:
        return ""
    return description.strip().splitlines()[0].replace("|", "\\|")


def _mark(present: bool) -> str:
    return "yes" if present else ""


def main() -> int:
    server = create_server(backend=None, policy=policy.Policy(frozenset(policy.ALL_CAPABILITIES)))
    tools = sorted(server._tool_manager.list_tools(), key=lambda t: t.name)

    lines = [
        "| Tool | Capability | core | google | Description |",
        "|---|---|---|---|---|",
    ]
    for t in tools:
        capability = _capabilities.TOOL_CAPABILITIES.get(t.name)
        lines.append(
            f"| `{t.name}` | {capability or '-'} | "
            f"{_mark(t.name in _flavours.CORE_TOOLS | _flavours.ALWAYS_REGISTERED)} | "
            f"{_mark(t.name in _flavours.GOOGLE_TOOLS | _flavours.ALWAYS_REGISTERED)} | "
            f"{_first_line(t.description)} |")
    lines.append("")
    md = "\n".join(lines)

    readme = ROOT / "README.md"
    text = readme.read_text()
    start, end = "<!-- TOOLS:START -->", "<!-- TOOLS:END -->"
    if start not in text or end not in text:
        print(md)
        print("README.md has no TOOLS:START/END markers - printed instead of spliced",
             file=sys.stderr)
        return 1
    text = text[:text.index(start) + len(start)] + "\n" + md + "\n" + text[text.index(end):]
    readme.write_text(text)
    print(f"README tool table regenerated - {len(tools)} tools", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
