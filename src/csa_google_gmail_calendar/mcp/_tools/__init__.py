"""One module per tool axis. `create_server` composes them; none of them knows the others.

The split is what lets a disabled capability be a *registration-time* absence rather than a
registered-but-refusing tool - see `server.py`'s module docstring. `config.py` (task 13) is
reserved but not yet wired in - see its own module docstring.
"""
from .auth import register_auth_tools
from .calendar_read import register_calendar_read_tools
from .calendar_write import register_calendar_write_tools
from .mail_read import register_mail_read_tools
from .mail_send import register_mail_send_tools
from .mail_write import register_mail_write_tools

__all__ = [
    "register_auth_tools",
    "register_calendar_read_tools",
    "register_calendar_write_tools",
    "register_mail_read_tools",
    "register_mail_send_tools",
    "register_mail_write_tools",
]
