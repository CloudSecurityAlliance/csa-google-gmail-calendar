"""One module per tool axis. `create_server` composes them; none of them knows the others.

The split is what lets a disabled capability be a *registration-time* absence rather than a
registered-but-refusing tool - see `server.py`'s module docstring. `config.py`/`demo.py`/
`feedback.py` (task 13) register the configuration surface: `describe_configuration`,
`demonstration_plan`, `report_a_problem` - none gated by a capability, all exempt from flavour
filtering (`_flavours.ALWAYS_REGISTERED`).
"""
from .auth import register_auth_tools
from .calendar_read import register_calendar_read_tools
from .calendar_write import register_calendar_write_tools
from .config import register_config_tools
from .demo import register_demo_tools
from .feedback import register_feedback_tools
from .mail_read import register_mail_read_tools
from .mail_send import register_mail_send_tools
from .mail_write import register_mail_write_tools

__all__ = [
    "register_auth_tools",
    "register_calendar_read_tools",
    "register_calendar_write_tools",
    "register_config_tools",
    "register_demo_tools",
    "register_feedback_tools",
    "register_mail_read_tools",
    "register_mail_send_tools",
    "register_mail_write_tools",
]
