"""Reserved for `describe_configuration` (task 13 of the plan).

Not wired into `create_server` yet - there is nothing here to register. Left as an empty
module rather than a `register_config_tools` stub that does nothing, because a stub calling
convention decided now, before the tool it is meant to hold exists, is exactly the kind of
structure-ahead-of-experience this project's own sibling repos warn against (see
CINO-Platform-Engineering's CORE-PRINCIPLE.md). Task 13 writes both the function and its
registration call together.

What it will need to say, decided now while the reasoning is fresh (task-10-report.md has the
full analysis): enabled capabilities, granted OAuth scopes, the active flavour and what it
hides, and whether `CSA_GGC_ATTACH_DIR` is configured - the path, never its contents. Never a
token path, a token's contents, or anything a plain refusal would not already disclose.
"""
from __future__ import annotations
