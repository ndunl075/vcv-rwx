"""selector.main.changed_slots end to end on the stand-in tree: the pinned state is built from a pinned tree, a patched
tree is built in a private directory and compared in a fresh process."""
import json
import os
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import slotkit  # noqa: E402


class ChangedSlots(unittest.TestCase):
    def test_patch_against_pinned_state(self):
        pinned = slotkit.make_tree(slotkit.BASE)
        tmp = tempfile.mkdtemp(prefix="rwxtmp-")
        state = os.path.join(pinned, "state.json.gz")
        slotkit.run("""
            import sys
            sys.argv = ["state", %r, %r]
            from selector import state
            state.main()
        """ % (pinned, state), pinned)
        plain = textwrap.dedent(slotkit.BASE["sqlglot/core.py"])
        core = plain.replace("LEVEL = 3", "LEVEL = 4")
        out = slotkit.run("""
            import json, sys
            from selector import main
            class P:   # the parts of a plan the comparison reads
                diffs = {}
                new_files = {"sqlglot/core.py": %r.encode()}
            class C:
                state_path = %r
            main.verify.base()
            changed = main.changed_slots(C, P, 0, 60)
            print(json.dumps(sorted(k.replace("\\t", " ") for k in changed)))
            # a patch that changes nothing the import state holds
            P.new_files = {"sqlglot/core.py": %r.encode()}
            print(json.dumps(sorted(main.changed_slots(C, P, 1, 60))))
        """ % (core, state, plain + "\n# a comment\n"), pinned,
            env={"RWX_PINNED": pinned, "RWX_TMP": tmp})
        first, second = [json.loads(x) for x in out.strip().split("\n")]
        self.assertIn("M sqlglot.core LEVEL", first)
        self.assertIn("M sqlglot.other COPY", first)
        self.assertEqual(second, [], second[:8])


if __name__ == "__main__":
    unittest.main()
