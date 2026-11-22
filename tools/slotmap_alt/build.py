"""Build maps/slots.json.gz from the pinned commit (read by selector/slotmap.py).

usage: build.py <pinned checkout> <work dir> <maps/lines.json.gz> <out.json.gz> [jobs]
Every test is traced alone in a fresh pytest process and once more inside a whole-suite session; a
slot's readers are the union.  A test that fails in a tracing run is listed as "unknown" (the selector
then treats it as a reader of everything).
"""
import gzip
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))


def env_for(pinned, out, extra):
    env = dict(os.environ, PYTHONPATH=HERE + os.pathsep + pinned, SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1", RWX_SLOT_OUT=out)
    for k in ("RWX_SLOT_TEST", "RWX_SLOT_MODE", "RWX_SLOT_KEYS"):
        env.pop(k, None)
    env.update(extra)
    return env


def encode(indexes):
    xs = sorted(indexes)
    parts = []
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
            j += 1
        parts.append(str(xs[i]) if i == j else "%d-%d" % (xs[i], xs[j]))
        i = j + 1
    return ",".join(parts)


def read(out, keys, ids):
    """-> (slot key -> set of test indexes, failed test ids, clean end)"""
    readers, failed, ended = {}, set(), False
    for name in sorted(os.listdir(out)):
        full = os.path.join(out, name)
        if name.endswith(".log") and name != "pytest.log":
            with open(full, encoding="utf-8", errors="replace") as f:
                for line in f:
                    k, _, t = line.rstrip("\n").partition("\t")
                    if not t.isdigit():
                        continue
                    key = keys[int(k)] if k.isdigit() else k.replace("\x1f", "\t")
                    readers.setdefault(key, set()).add(int(t))
        elif name.startswith("results."):
            with open(full, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line[:2] in ("F ", "C "):
                        failed.add(line[2:].strip())
                    elif line.startswith("E "):
                        ended = line[2:].strip() in ("0", "1")
    return readers, failed, ended


def main():
    pinned, work, lines_map, outp = sys.argv[1:5]
    jobs = int(sys.argv[5]) if len(sys.argv) > 5 else 4
    with gzip.open(lines_map, "rt") as f:
        ids = json.load(f)["ids"]
    os.makedirs(work, exist_ok=True)
    ids_file = os.path.join(work, "ids.json")
    with open(ids_file, "w") as f:
        json.dump(ids, f)
    keys_file = os.path.join(work, "keys.json")
    if not os.path.exists(keys_file):
        # the key list: import every sqlglot module (as selector/state.py does) with the tracer on
        code = ("import sys, os, json, importlib\nsys.path.insert(0, %r)\nsys.path.insert(0, %r)\n"
                "from selector.state import module_names\n"
                "for n in module_names(%r):\n    importlib.import_module(n)\n"
                "import rwx_slots\njson.dump(sorted(set(rwx_slots.WRAPPED)), open(%r, 'w'))\n") % (
                    os.path.dirname(os.path.dirname(HERE)), pinned, pinned, keys_file)
        os.makedirs(os.path.join(work, "keys"), exist_ok=True)
        subprocess.run([sys.executable, "-c", code], cwd=pinned, env=env_for(pinned, os.path.join(work, "keys"), {}), check=True)
    with open(keys_file) as f:
        keys = json.load(f)
    common = {"RWX_SLOT_KEYS": keys_file, "RWX_SLOT_IDS": ids_file}
    base = [sys.executable, "-m", "pytest", "-p", "rwx_slots_plugin", "-p", "no:cacheprovider", "-q", "--tb=short", "--timeout=3000"]
    todo = [None] + list(range(len(ids)))
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not todo:
                    return
                k = todo.pop(0)
            if k is None:
                out = os.path.join(work, "session")
                extra, args = dict(common, RWX_SLOT_MODE="session"), ["tests"]
            else:
                out = os.path.join(work, "cold", str(k))
                extra, args = dict(common, RWX_SLOT_TEST=str(k)), [ids[k]]
            if os.path.exists(os.path.join(out, "done")):
                continue
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "pytest.log"), "w") as log:
                subprocess.run(base + args, cwd=pinned, env=env_for(pinned, out, extra), stdout=log, stderr=subprocess.STDOUT)
            open(os.path.join(out, "done"), "w").close()
            if k is None or k % 100 == 0:
                print("done", "session" if k is None else k, flush=True)

    threads = [threading.Thread(target=worker) for _ in range(jobs)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    readers, sfailed, sended = read(os.path.join(work, "session"), keys, ids)
    unknown = set()
    if not sended:
        raise SystemExit("the session run did not end cleanly")
    for k, t in enumerate(ids):
        r, failed, ended = read(os.path.join(work, "cold", str(k)), keys, ids)
        if not ended or failed or t in sfailed:
            unknown.add(k)
        for key, who in r.items():
            readers.setdefault(key, set()).update(who)
    sets, index, slots = [], {}, {}
    for key in sorted(set(keys) | set(readers)):
        who = readers.get(key)
        if not who:
            slots[key] = -1
            continue
        enc = encode(who)
        if enc not in index:
            index[enc] = len(sets)
            sets.append(enc)
        slots[key] = index[enc]
    with gzip.GzipFile(outp, "wb", mtime=0) as f:
        f.write(json.dumps({"ids": ids, "sets": sets, "slots": slots, "unknown": sorted(unknown), "session": True},
                           separators=(",", ":"), sort_keys=True).encode())
    print("%d slots, %d read, %d sets, %d unknown tests" % (len(slots), sum(1 for v in slots.values() if v >= 0), len(sets), len(unknown)))


if __name__ == "__main__":
    main()
