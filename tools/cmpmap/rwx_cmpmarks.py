"""pytest plugin for the whole-suite comparison profile: tells the tracer which test is running and which failed."""
import os
import sys

import pytest


def pytest_sessionstart(session):
    note = getattr(sys, "rwx_cmp_note", None)
    if note:
        note("P", "")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logstart(nodeid, location):
    mark = getattr(sys, "rwx_cmp_mark", None)
    if mark:
        mark(nodeid)


@pytest.hookimpl(trylast=True)
def pytest_runtest_logfinish(nodeid, location):
    mark = getattr(sys, "rwx_cmp_mark", None)
    if mark:
        mark(None)


def pytest_runtest_logreport(report):
    note = getattr(sys, "rwx_cmp_note", None)
    if note and report.outcome == "failed":
        note("F", report.nodeid)
