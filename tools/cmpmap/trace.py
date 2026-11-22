"""Comparison profiles of the slow tests on the pinned commit, inside the labeling image.

    trace.py <clean> <out.json> <test id>...      each test alone in a fresh pytest process, twice
                                                  (two hash seeds); a site keeps what held in both
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    clean, out = sys.argv[1], sys.argv[2]
    root = "/p"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(clean, root, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    result = {}
    for tid in sys.argv[3:]:
        sites = {}
        runs = []
        for seed in ("1", "2"):
            d = "/tmp/cmp"
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
            env = dict(os.environ, PYTHONPATH=HERE + os.pathsep + root, SKIP_INTEGRATION="0", PYTHONHASHSEED=seed,
                       PYTHONDONTWRITEBYTECODE="1", RWX_CMP_DIR=d, RWX_CMP_ROOT=root)   # no bytecode: sources are rewritten
            p = subprocess.run([sys.executable, "-m", "pytest", tid, "-q", "-p", "no:cacheprovider"], cwd=root, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            runs.append(p.returncode)
            print(tid, "seed", seed, "rc", p.returncode, p.stdout[-120:].decode("utf-8", "replace").strip(), file=sys.stderr, flush=True)
            for n in os.listdir(d):
                for line in open(os.path.join(d, n)):
                    site, op, m, cnt = line.split()
                    cur = sites.get(site)
                    sites[site] = [op, int(m) if cur is None else cur[1] & int(m), int(cnt) + (cur[2] if cur else 0)]
        result[tid] = {"rc": runs, "sites": sites}
    with open(out, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
