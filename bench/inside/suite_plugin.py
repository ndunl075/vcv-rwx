"""pytest plugin run inside the labeling container.

Records, per test node id, whether it failed: any failed/error report in
setup, call or teardown (SUBFAILED subtest reports included) fails the test.
A collection error fails every test of that file (the caller maps it by file).
Writes JSON to $RESULT_PATH at session end.
"""
import json
import os
import signal

import pytest

CPU_LIMIT = float(os.environ.get("CPU_LIMIT", "150"))


class CpuTimeout(Exception):
    pass


def _on_prof(signum, frame):
    raise CpuTimeout(f"test used more than {CPU_LIMIT}s of CPU")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    # a CPU-time limit: immune to machine load, unlike a wall-clock timeout
    signal.signal(signal.SIGPROF, _on_prof)
    signal.setitimer(signal.ITIMER_PROF, CPU_LIMIT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)


class Recorder:
    def __init__(self):
        self.failed = {}      # nodeid -> reason
        self.seen = set()
        self.collect_failed = {}  # file -> repr

    def pytest_collectreport(self, report):
        if report.failed:
            self.collect_failed[report.nodeid] = str(report.longrepr)[-500:]

    def pytest_runtest_logreport(self, report):
        nid = report.nodeid
        self.seen.add(nid)
        if report.outcome == "failed":
            self.failed.setdefault(nid, f"{report.when}")

    def pytest_sessionfinish(self, session, exitstatus):
        out = {
            "failed": sorted(self.failed),
            "seen": sorted(self.seen),
            "collect_failed": sorted(self.collect_failed),
            "exitstatus": int(exitstatus),
        }
        with open(os.environ["RESULT_PATH"], "w") as f:
            json.dump(out, f)


def pytest_configure(config):
    config.pluginmanager.register(Recorder(), "rwx-recorder")
