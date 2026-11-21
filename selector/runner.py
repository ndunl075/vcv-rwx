"""Run sqlglot tests directly with unittest, without pytest's start-up and collection.

`run_ids` runs inside an already forked child: it imports the test modules named by
the ids from `root`, builds one unittest suite (in the order of `ids`) and reports,
for every id, whether the test failed.  "Failed" is the union of everything pytest
counts as failed: failure, error, a failing subtest, an error in setUpClass /
setUpModule / tearDownClass, an unexpected success, a test file that cannot be
imported.  Skips and expected failures are not failures.
"""
import importlib
import os
import sys
import unittest


def split_id(nodeid):
    path, cls, meth = nodeid.split("::")
    return path[:-3].replace("/", "."), cls, meth


class _Result(unittest.TestResult):
    def __init__(self, wanted, emit):
        super().__init__()
        self.wanted = wanted  # set of (module, class, method)
        self.emit = emit
        self.failed = set()
        self.seen = set()

    @staticmethod
    def key(test):
        return (type(test).__module__, type(test).__qualname__, test._testMethodName)

    def _fail(self, test):
        if isinstance(test, unittest.TestCase):
            k = self.key(test)
            self.failed.add(k)
        else:
            # setUpClass / setUpModule / tearDownClass holder: every test in scope fails
            desc = str(test)
            for k in self.wanted:
                if (k[0] in desc and k[1] in desc) or ("setUpModule" in desc or "tearDownModule" in desc) and k[0] in desc:
                    self.failed.add(k)
            if not any(k in self.failed for k in self.wanted):
                self.failed.update(self.wanted)

    def addError(self, test, err):
        super().addError(test, err)
        self._fail(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._fail(test)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._fail(test)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            if issubclass(err[0], unittest.SkipTest):
                return
            self._fail(test)

    def stopTest(self, test):
        super().stopTest(test)
        if isinstance(test, unittest.TestCase):
            k = self.key(test)
            self.seen.add(k)
            self.emit(k, k in self.failed)


def run_ids(ids, root, emit):
    """Run `ids` (nodeids) in this process.  emit((mod, cls, meth), failed) is called per test."""
    os.environ.setdefault("SKIP_INTEGRATION", "0")
    wanted = {split_id(i) for i in ids}
    by_mod = {}
    for k in sorted(wanted, key=lambda k: ids.index("%s::%s::%s" % (k[0].replace(".", "/") + ".py", k[1], k[2]))):
        by_mod.setdefault(k[0], []).append(k)
    suite = unittest.TestSuite()
    broken = set()
    for mod_name, keys in by_mod.items():
        try:
            mod = importlib.import_module(mod_name)
        except BaseException:  # file cannot be imported => every test in it fails
            for k in keys:
                broken.add(k)
            continue
        by_cls = {}
        for k in keys:
            by_cls.setdefault(k[1], []).append(k)
        for cls_name, ks in by_cls.items():
            cls = getattr(mod, cls_name, None)
            if cls is None:
                broken.update(ks)
                continue
            for k in ks:
                if not hasattr(cls, k[2]):
                    broken.add(k)
                    continue
                suite.addTest(cls(k[2]))
    for k in broken:
        emit(k, True)
    res = _Result(wanted, emit)
    suite.run(res)
    for k in wanted - res.seen - broken:
        # never reached stopTest (e.g. setUpClass failed): pytest reports an error
        emit(k, True)
