"""pytest plugin for the whole-suite recording run: record only while a wanted test runs.
RWX_CMP_TARGETS  file with one node id per line."""
import os

import pytest

import _rwx_cmp

with open(os.environ["RWX_CMP_TARGETS"]) as _f:
    _want = {line.strip() for line in _f if line.strip()}
_res = os.open(os.environ["RWX_CMP_OUT"] + "/results.%d" % os.getpid(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if os.environ.get("RWX_CMP_MODE") == "session":
        _rwx_cmp.set_test(item.nodeid if item.nodeid in _want else None)
    try:
        return (yield)
    finally:
        if os.environ.get("RWX_CMP_MODE") == "session":
            _rwx_cmp.set_test(None)


def pytest_runtest_logreport(report):
    if report.nodeid in _want and report.outcome == "failed":
        os.write(_res, ("F %s\n" % report.nodeid).encode())


def pytest_sessionfinish(session, exitstatus):
    os.write(_res, ("E %d\n" % int(exitstatus)).encode())
