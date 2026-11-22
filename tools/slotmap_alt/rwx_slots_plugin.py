"""pytest plugin for the tracing runs.  RWX_SLOT_IDS: JSON list of test ids (index = position).
RWX_SLOT_MODE=session: the plugin sets the traced test around every test; otherwise the whole
process belongs to RWX_SLOT_TEST.  Failing tests are written to results.<pid> in RWX_SLOT_OUT."""
import json
import os

import pytest

import rwx_slots

with open(os.environ["RWX_SLOT_IDS"]) as _f:
    _index = {t: str(i) for i, t in enumerate(json.load(_f))}
_session = os.environ.get("RWX_SLOT_MODE") == "session"
_res = os.open(os.path.join(os.environ["RWX_SLOT_OUT"], "results.%d" % os.getpid()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    if _session:
        rwx_slots.set_test(_index.get(item.nodeid))
    try:
        return (yield)
    finally:
        if _session:
            rwx_slots.set_test(None)


def pytest_runtest_logreport(report):
    if report.outcome == "failed":
        os.write(_res, ("F %s\n" % report.nodeid).encode("utf-8", "backslashreplace"))


def pytest_collectreport(report):
    if report.failed:
        os.write(_res, ("C %s\n" % report.nodeid).encode("utf-8", "backslashreplace"))


def pytest_sessionfinish(session, exitstatus):
    os.write(_res, ("E %d\n" % int(exitstatus)).encode())
