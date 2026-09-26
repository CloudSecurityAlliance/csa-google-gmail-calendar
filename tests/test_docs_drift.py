"""Documentation drift as a testable property (TESTING.md): a README tool table nobody checks
is a promise that decays silently. Adapted from the task-13 brief's own illustrative sample,
extended to cover every `CSA_GGC_*` variable the code actually reads, not only the two named
there."""
import pathlib
import re

from csa_google_gmail_calendar import policy
from csa_google_gmail_calendar.mcp import create_server

README = pathlib.Path(__file__).parent.parent / "README.md"
SRC = pathlib.Path(__file__).parent.parent / "src"


def test_readme_lists_every_tool_the_server_registers():
    """A README tool table nobody checks is a promise that decays silently (TESTING.md).
    Built with every capability enabled, matching `test_mcp_capabilities.py`'s own reasoning:
    this is about the full universe of tools this server can ever register, not one
    deployment's narrowed policy."""
    p = policy.Policy(frozenset(policy.ALL_CAPABILITIES))
    registered = {t.name for t in create_server(backend=None, policy=p)._tool_manager.list_tools()}
    readme = README.read_text()
    documented = set(re.findall(r"`([a-z_]+)`", readme))
    assert not registered - documented, f"undocumented: {sorted(registered - documented)}"


def test_every_env_var_the_code_reads_is_in_the_readme():
    used: set[str] = set()
    for f in SRC.rglob("*.py"):
        used |= set(re.findall(r'"(CSA_GGC_[A-Z_]+)"', f.read_text()))
    readme = README.read_text()
    missing = {v for v in used if v not in readme}
    assert not missing, f"undocumented environment variables: {sorted(missing)}"


def test_every_env_var_the_readme_documents_is_actually_read_somewhere():
    """The reverse direction - a documented variable this codebase never reads is a promise
    the README makes that the code cannot keep."""
    readme = README.read_text()
    documented = set(re.findall(r"(CSA_GGC_[A-Z_]+)", readme))
    used: set[str] = set()
    for f in SRC.rglob("*.py"):
        used |= set(re.findall(r'"(CSA_GGC_[A-Z_]+)"', f.read_text()))
    phantom = documented - used
    assert not phantom, f"README documents variables nothing reads: {sorted(phantom)}"


def test_todo_md_mentions_the_first_implementation_plan_is_complete_or_open_items_remain():
    """Not a strict drift check (TODO.md's own content is a judgement call, not a derivable
    set) - but this repo's own convention (CLAUDE.md: 'a new top-level doc gets a row... any
    work you leave open gets a line in TODO.md') means TODO.md must at minimum exist and be
    non-empty after this task, the same bar every prior task in this plan held itself to."""
    todo = (pathlib.Path(__file__).parent.parent / "TODO.md").read_text()
    assert todo.strip()


def test_claude_md_no_longer_claims_nothing_is_implemented():
    """CLAUDE.md predates any code in this repo and said so explicitly ('There is no src/.
    Nothing is implemented.') - a stale claim like that is exactly the kind of drift this test
    file exists to catch project-wide, not only for the tool table."""
    claude_md = (pathlib.Path(__file__).parent.parent / "CLAUDE.md").read_text()
    assert "Nothing is implemented" not in claude_md
