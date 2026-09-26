"""`_logging.py`: level parsing and stderr handler wiring."""
import logging

import pytest

from csa_google_gmail_calendar.mcp import _logging


def test_level_from_env_defaults_to_warning():
    assert _logging.level_from_env({}) == "WARNING"


def test_level_from_env_reads_the_variable_case_insensitively():
    assert _logging.level_from_env({"CSA_GGC_LOG_LEVEL": "debug"}) == "DEBUG"


def test_level_from_env_refuses_to_guess_at_an_unrecognised_value():
    with pytest.raises(ValueError, match="CSA_GGC_LOG_LEVEL"):
        _logging.level_from_env({"CSA_GGC_LOG_LEVEL": "verbose"})


def test_configure_attaches_exactly_one_handler_even_called_twice():
    _logging.configure({"CSA_GGC_LOG_LEVEL": "INFO"})
    _logging.configure({"CSA_GGC_LOG_LEVEL": "INFO"})
    logger = logging.getLogger(_logging._ROOT)
    ours = [h for h in logger.handlers if getattr(h, "_csa_ggc", False)]
    assert len(ours) == 1
    assert logger.level == logging.INFO
    assert logger.propagate is False
