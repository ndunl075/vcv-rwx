"""Build maps/cmp.json.gz from the pinned commit (see selector/cmpproof.py for what it proves).

usage: build.py <pinned checkout> <work dir> <targets file> <out.json.gz> [jobs]
Each target test is recorded twice: alone in a fresh pytest process (everything that process runs,
imports included) and inside one whole-suite session.  A site keeps an operator only if it agreed at
every execution of both recordings.  A target that fails in either recording gets no entry.
"""
import gzip
import json
import os
import subprocess
import sys
import threading

OPS = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"]
HERE = os.path.dirname(os.path.abspath(__file__))


def run(tree, out, mode, args, test=None):
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=HERE + os.pathsep + tree, SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1",
               RWX_CMP_OUT=out, RWX_CMP_MODE=mode, PYTHONHASHSEED="0")
    env.pop("RWX_CMP_TEST", None)
    if test:
        env["RWX_CMP_TEST"] = test
    with open(os.path.join(out, "pytest.log"), "w") as log:
        subprocess.run([sys.executable, "-m", "pytest", "-p", "rwx_cmp_plugin", "-p", "no:cacheprovider", "-q", "--tb=short",
                        "--timeout=3000"] + args, cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT)


def read(out):
    """(test -> site index -> mask, failed tests, session ended cleanly)"""
    masks, failed, ended = {}, set(), False
    for name in sorted(os.listdir(out)):
        full = os.path.join(out, name)
        if name.endswith(".log") and name != "pytest.log":
            with open(full, encoding="utf-8", errors="replace") as f:
                for line in f:
                    head, _, test = line.rstrip("\n").partition("\t")
                    i, m = head.split(" ")
                    d = masks.setdefault(test, {})
                    d[int(i)] = d.get(int(i), 1023) & int(m)
        elif name.startswith("results."):
            with open(full) as f:
                for line in f:
                    if line.startswith("F "):
                        failed.add(line[2:].strip())
                    elif line.startswith("E "):
                        ended = line[2:].strip() in ("0", "1")
    return masks, failed, ended


def main():
    pinned, work, targets_file, outp = sys.argv[1:5]
    jobs = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    with open(targets_file) as f:
        targets = [line.strip() for line in f if line.strip()]
    tree = os.path.join(work, "tree")
    if not os.path.isdir(tree):
        subprocess.run([sys.executable, os.path.join(HERE, "instrument.py"), pinned, tree], check=True)
    with open(os.path.join(tree, "rwx_cmp_sites.json")) as f:
        sites = json.load(f)
    os.environ["RWX_CMP_TARGETS"] = os.path.abspath(targets_file)
    todo = [("session", None)] + [("cold", t) for t in targets]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not todo:
                    return
                mode, t = todo.pop(0)
            if mode == "session":
                run(tree, os.path.join(work, "session"), "session", ["tests"])
            else:
                run(tree, os.path.join(work, "cold", str(targets.index(t))), "cold", [t], test=t)
            print("done", mode, t or "", flush=True)

    threads = [threading.Thread(target=worker) for _ in range(jobs)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    smasks, sfailed, sended = read(os.path.join(work, "session"))
    out = {}
    skipped = []
    for k, t in enumerate(targets):
        cmasks, cfailed, cended = read(os.path.join(work, "cold", str(k)))
        if not sended or not cended or t in sfailed or t in cfailed or t not in cmasks:
            skipped.append(t)
            continue
        cold, sess = cmasks[t], smasks.get(t, {})
        entry = {}
        for i in set(cold) | set(sess):
            m = cold.get(i, 1023) & sess.get(i, 1023)
            site, op = sites[i]
            entry[site] = [op, m]
        out[t] = entry
    with gzip.open(outp, "wt", compresslevel=9) as f:
        json.dump({"tests": out, "ops": OPS}, f, sort_keys=True, separators=(",", ":"))
    print("%d tests, %d skipped %s" % (len(out), len(skipped), skipped))


if __name__ == "__main__":
    main()
