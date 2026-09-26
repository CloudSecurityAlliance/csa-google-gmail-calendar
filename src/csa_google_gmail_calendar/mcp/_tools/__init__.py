"""One module per tool axis. `create_server` composes them; none of them knows the others.

The split is what lets a disabled capability be a *registration-time* absence rather than a
registered-but-refusing tool - see `server.py`'s module docstring. `mail_read.py`/
`mail_write.py`/`mail_send.py` (task 11) and `calendar_read.py`/`calendar_write.py` (task 12)
join this list as they land; `config.py` (task 13) is reserved but not yet wired in - see its
own module docstring.
"""
from .auth import register_auth_tools

__all__ = ["register_auth_tools"]
