"""Documentation drift as a testable property (TESTING.md): a README tool table nobody checks
is a promise that decays silently. Adapted from the task-13 brief's own illustrative sample,
extended to cover every `CSA_GGC_*` variable the code actually reads, not only the two named
there."""
import pathlib
import re

from csa_google_gmail_calendar import _attachments, policy
from csa_google_gmail_calendar.mcp import create_server

README = pathlib.Path(__file__).parent.parent / "README.md"
SRC = pathlib.Path(__file__).parent.parent / "src"
CLI = SRC / "csa_google_gmail_calendar" / "mcp" / "cli.py"


def _readme_tools_table_names(readme_text: str) -> set[str]:
    """The tool names in the GENERATED table specifically (between the `TOOLS:START`/`:END`
    markers `scripts/generate_tool_table.py` writes), not every backticked identifier anywhere
    in the README - a tool mentioned once in prose (or named inside another row's Description
    cell) must not satisfy this check on the table's behalf. Only the first cell of each table
    row counts, since that is the one column that is the tool's own name."""
    start, end = "<!-- TOOLS:START -->", "<!-- TOOLS:END -->"
    assert start in readme_text and end in readme_text, "README is missing the TOOLS markers"
    table = readme_text[readme_text.index(start) + len(start):readme_text.index(end)]
    names: set[str] = set()
    for line in table.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        m = re.fullmatch(r"`([a-z_]+)`", cells[0])
        if m:
            names.add(m.group(1))
    return names


def test_readme_tool_table_matches_the_live_registry_exactly():
    """A README tool table nobody checks is a promise that decays silently (TESTING.md).
    Checked in BOTH directions against the GENERATED table specifically: a tool this server
    registers but the table omits (the old, one-directional check), AND a tool the table still
    lists after it stopped being registered (a tool removed, or renamed, leaving a stale row
    behind) - the previous version of this test caught only the first. Built with every
    capability enabled, matching `test_mcp_capabilities.py`'s own reasoning: this is about the
    full universe of tools this server can ever register, not one deployment's narrowed
    policy."""
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    registered = {t.name for t in create_server(backend=None, policy=p)._tool_manager.list_tools()}
    documented = _readme_tools_table_names(README.read_text(encoding="utf-8"))
    assert registered == documented, (
        f"registered but missing from the table: {sorted(registered - documented)}; "
        f"in the table but no longer registered: {sorted(documented - registered)}")


def test_every_env_var_the_code_reads_is_in_the_readme():
    used: set[str] = set()
    for f in SRC.rglob("*.py"):
        used |= set(re.findall(r'"(CSA_GGC_[A-Z_]+)"', f.read_text(encoding="utf-8")))
    readme = README.read_text(encoding="utf-8")
    missing = {v for v in used if v not in readme}
    assert not missing, f"undocumented environment variables: {sorted(missing)}"


def test_every_env_var_the_readme_documents_is_actually_read_somewhere():
    """The reverse direction - a documented variable this codebase never reads is a promise
    the README makes that the code cannot keep."""
    readme = README.read_text(encoding="utf-8")
    documented = set(re.findall(r"(CSA_GGC_[A-Z_]+)", readme))
    used: set[str] = set()
    for f in SRC.rglob("*.py"):
        used |= set(re.findall(r'"(CSA_GGC_[A-Z_]+)"', f.read_text(encoding="utf-8")))
    phantom = documented - used
    assert not phantom, f"README documents variables nothing reads: {sorted(phantom)}"


def test_default_attach_dir_matches_the_readme_and_the_cli_help():
    """The two env-var tests above catch a variable the README forgets, not a default it gets
    WRONG - `CSA_GGC_ATTACH_DIR`'s default lives in three places (`DEFAULT_ATTACH_DIR`, the
    README's table row, and `cli.py`'s `--help` text) that must all name the same directory, and
    nothing before this test compared them. This branch renamed the default from
    `~/Documents/CSA-Outbox` to `~/CSA-Uploads` and is the proof that one of the three can be
    missed - catch it here instead of in the next whole-branch review."""
    default = _attachments.DEFAULT_ATTACH_DIR
    readme = README.read_text(encoding="utf-8")
    readme_row = next(line for line in readme.splitlines() if "CSA_GGC_ATTACH_DIR" in line)
    assert default in readme_row, (
        f"README's CSA_GGC_ATTACH_DIR row does not mention {default!r}: {readme_row!r}")
    cli_help = CLI.read_text(encoding="utf-8")
    assert default in cli_help, f"cli.py --help text does not mention {default!r}"


def test_default_download_dir_matches_the_readme_and_the_cli_help():
    """Same drift as above, checked for `CSA_GGC_DOWNLOAD_DIR` - it did not move on this branch,
    but the three-place agreement it depends on is exactly as unchecked, so it is equally cheap
    to guard now rather than the next time a default changes."""
    default = _attachments.DEFAULT_DOWNLOAD_DIR
    readme = README.read_text(encoding="utf-8")
    readme_row = next(line for line in readme.splitlines() if "CSA_GGC_DOWNLOAD_DIR" in line)
    assert default in readme_row, (
        f"README's CSA_GGC_DOWNLOAD_DIR row does not mention {default!r}: {readme_row!r}")
    cli_help = CLI.read_text(encoding="utf-8")
    assert default in cli_help, f"cli.py --help text does not mention {default!r}"


def test_todo_md_mentions_the_first_implementation_plan_is_complete_or_open_items_remain():
    """Not a strict drift check (TODO.md's own content is a judgement call, not a derivable
    set) - but this repo's own convention (CLAUDE.md: 'a new top-level doc gets a row... any
    work you leave open gets a line in TODO.md') means TODO.md must at minimum exist and be
    non-empty after this task, the same bar every prior task in this plan held itself to."""
    todo = (pathlib.Path(__file__).parent.parent / "TODO.md").read_text(encoding="utf-8")
    assert todo.strip()


def test_claude_md_no_longer_claims_nothing_is_implemented():
    """CLAUDE.md predates any code in this repo and said so explicitly ('There is no src/.
    Nothing is implemented.') - a stale claim like that is exactly the kind of drift this test
    file exists to catch project-wide, not only for the tool table."""
    claude_md = (pathlib.Path(__file__).parent.parent / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Nothing is implemented" not in claude_md
