"""pytest plugin for the whole-suite trace: mark the start of every test in the trace."""
import sys

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logstart(nodeid, location):
    mark = getattr(sys, "rwx_mark", None)
    if mark:
        mark(nodeid)


def pytest_configure(config):
    # tag every pool task with the test that submits it
    from concurrent.futures import ProcessPoolExecutor

    import sitecustomize

    submit = ProcessPoolExecutor.submit

    def traced_submit(self, fn, /, *args, **kwargs):
        return submit(self, sitecustomize.Traced(fn, getattr(sys, "rwx_current", None)), *args, **kwargs)

    ProcessPoolExecutor.submit = traced_submit
