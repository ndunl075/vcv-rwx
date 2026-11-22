"""pytest plugin for the whole-suite trace: mark the start of every test in the trace."""
import sys

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logstart(nodeid, location):
    mark = getattr(sys, "rwx_mark", None)
    if mark:
        mark(nodeid)
