"""pytest plugin for the verification runs: keep only the wanted tests, stream every report.

RWX_WANT  file with one node id per line (the tests to keep)
RWX_OUT   file that receives one line per event, written at once so that a killed run leaves
          the verdicts it reached:
            R <when> <outcome> <duration> <nodeid>     a test report (subtest reports included)
            C <nodeid>                                 a collector that failed
            K <count>                                  tests kept after collection
            E <exitstatus>                             session end
"""
import os

_fd = os.open(os.environ["RWX_OUT"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
with open(os.environ["RWX_WANT"]) as _f:
    _want = set(_f.read().split("\n"))


def _put(line):
    os.write(_fd, (line + "\n").encode("utf-8", "backslashreplace"))


def pytest_collectreport(report):
    if report.failed:
        _put("C %s" % report.nodeid)


def pytest_collection_modifyitems(session, config, items):
    keep = [i for i in items if i.nodeid in _want]
    drop = [i for i in items if i.nodeid not in _want]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
    _put("K %d" % len(keep))


def pytest_runtest_logreport(report):
    _put("R %s %s %.3f %s" % (report.when, report.outcome, getattr(report, "duration", 0.0) or 0.0, report.nodeid))


def pytest_sessionfinish(session, exitstatus):
    _put("E %d" % int(exitstatus))
