"""The MCP server for csa-google-gmail-calendar - `create_server`, and the `main()` console
entry point (`pyproject.toml`: `csa-google-gmail-calendar = "csa_google_gmail_calendar.mcp:main"`).

Importing this package requires the optional `mcp` extra (`pip install
csa-google-gmail-calendar[mcp]`); the rest of the library (`backend.py`, `mail.py`,
`calendar.py`, `policy.py`, `auth.py`) has no such requirement and is usable standalone.
"""
from __future__ import annotations

from .cli import main
from .server import create_server

__all__ = ["create_server", "main"]
