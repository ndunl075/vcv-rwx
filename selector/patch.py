"""Unified-diff parsing and strict application (no external `patch`, no git).

`parse(data)` returns a list of FileChange or raises Unparseable.  Anything this
module does not understand raises, and the caller falls back to a path that cannot
miss.  `apply_hunks` applies hunks strictly (context must match exactly).
"""
import re


class Unparseable(Exception):
    pass


class Hunk:
    __slots__ = ("old_start", "old_len", "new_start", "new_len", "lines", "old_noeol", "new_noeol")

    def __init__(self, old_start, old_len, new_start, new_len):
        self.old_start, self.old_len, self.new_start, self.new_len = old_start, old_len, new_start, new_len
        self.lines = []  # (op, bytes) op in ' ', '-', '+'  (no trailing newline)
        self.old_noeol = False  # "\\ No newline at end of file" after the last old line
        self.new_noeol = False


class FileChange:
    def __init__(self):
        self.old_path = None  # None for add
        self.new_path = None  # None for delete
        self.kind = "modify"  # modify / add / delete / rename / copy / mode / binary / symlink
        self.hunks = []
        self.old_mode = None
        self.new_mode = None
        self.binary = False

    @property
    def path(self):
        return self.new_path if self.new_path is not None else self.old_path

    def __repr__(self):
        return "<FileChange %s %s->%s hunks=%d>" % (self.kind, self.old_path, self.new_path, len(self.hunks))


_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _unquote(p):
    p = p.strip()
    if p.startswith(b'"'):
        raise Unparseable("quoted path")
    return p


def _strip_prefix(p, n=1):
    if p == b"/dev/null":
        return None
    parts = p.split(b"/", n)
    if len(parts) <= n:
        raise Unparseable("path without prefix")
    return parts[n].decode("utf-8", "surrogateescape")


def parse(data):
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    changes = []
    cur = None
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.startswith(b"diff --git "):
            cur = FileChange()
            changes.append(cur)
            rest = ln[len(b"diff --git "):]
            m = re.match(rb"^a/(.+) b/(.+)$", rest)
            if not m or m.group(1) != m.group(2) and False:
                pass
            if m:
                cur.old_path = m.group(1).decode("utf-8", "surrogateescape")
                cur.new_path = m.group(2).decode("utf-8", "surrogateescape")
            else:
                raise Unparseable("diff --git header")
            i += 1
            continue
        if cur is None:
            if ln.startswith(b"--- ") or ln.startswith(b"Index: "):
                # plain unified diff without git headers
                if ln.startswith(b"Index: "):
                    i += 1
                    continue
                cur = FileChange()
                changes.append(cur)
                continue
            if ln.strip() == b"" or True:
                i += 1  # preamble (commit message etc.)
                continue
        if ln.startswith(b"new file mode"):
            cur.kind = "add"
            cur.new_mode = ln.split()[-1]
            cur.old_path = None
        elif ln.startswith(b"deleted file mode"):
            cur.kind = "delete"
            cur.old_mode = ln.split()[-1]
            cur.new_path = None
        elif ln.startswith(b"old mode"):
            cur.old_mode = ln.split()[-1]
        elif ln.startswith(b"new mode"):
            cur.new_mode = ln.split()[-1]
        elif ln.startswith(b"rename from "):
            cur.kind = "rename"
            cur.old_path = ln[len(b"rename from "):].decode("utf-8", "surrogateescape")
        elif ln.startswith(b"rename to "):
            cur.new_path = ln[len(b"rename to "):].decode("utf-8", "surrogateescape")
        elif ln.startswith(b"copy from ") or ln.startswith(b"copy to "):
            cur.kind = "copy"
        elif ln.startswith(b"similarity index") or ln.startswith(b"dissimilarity index") or ln.startswith(b"index "):
            pass
        elif ln.startswith(b"Binary files ") or ln.startswith(b"GIT binary patch"):
            cur.binary = True
            if ln.startswith(b"GIT binary patch"):
                # skip the payload up to the next diff header
                while i + 1 < n and not lines[i + 1].startswith(b"diff --git "):
                    i += 1
        elif ln.startswith(b"--- "):
            p = _unquote(ln[4:].split(b"\t")[0])
            if p == b"/dev/null":
                cur.kind = "add"
                cur.old_path = None
            elif cur.old_path is None and cur.kind != "add":
                cur.old_path = _strip_prefix(p)
        elif ln.startswith(b"+++ "):
            p = _unquote(ln[4:].split(b"\t")[0])
            if p == b"/dev/null":
                cur.kind = "delete"
                cur.new_path = None
            elif cur.new_path is None and cur.kind != "delete":
                cur.new_path = _strip_prefix(p)
        elif ln.startswith(b"@@"):
            m = _HUNK.match(ln)
            if not m:
                raise Unparseable("hunk header")
            os_, ol, ns, nl = m.groups()
            h = Hunk(int(os_), int(ol) if ol is not None else 1, int(ns), int(nl) if nl is not None else 1)
            i += 1
            old_seen = new_seen = 0
            while i < n and (old_seen < h.old_len or new_seen < h.new_len or lines[i].startswith(b"\\")):
                t = lines[i]
                if t.startswith(b"\\"):
                    if h.lines:
                        op = h.lines[-1][0]
                        if op in " -":
                            h.old_noeol = True
                        if op in " +":
                            h.new_noeol = True
                    i += 1
                    continue
                if t == b"":
                    # an empty context line that lost its leading space
                    op, body = " ", b""
                else:
                    op, body = chr(t[0]), t[1:]
                if op not in " -+":
                    raise Unparseable("hunk body line")
                h.lines.append((op, body))
                if op in " -":
                    old_seen += 1
                if op in " +":
                    new_seen += 1
                i += 1
            if old_seen != h.old_len or new_seen != h.new_len:
                raise Unparseable("hunk length mismatch")
            cur.hunks.append(h)
            continue
        else:
            if ln.strip() == b"":
                pass
            elif ln.startswith(b"-- ") or ln.startswith(b"--"):
                pass
            else:
                raise Unparseable("unknown line %r" % ln[:40])
        i += 1
    if not changes:
        raise Unparseable("no file changes")
    for c in changes:
        if c.kind == "modify" and c.old_path != c.new_path and c.old_path and c.new_path:
            c.kind = "rename"
        if c.kind == "modify" and not c.hunks and not c.binary and c.old_mode != c.new_mode:
            c.kind = "mode"
        if c.kind in ("modify", "add", "delete", "rename") and c.old_mode and c.old_mode.startswith(b"120"):
            c.kind = "symlink"
        if c.new_mode and c.new_mode.startswith(b"120"):
            c.kind = "symlink"
    return changes


def split_lines(content):
    """Lines without terminators, plus whether the last line had a final newline."""
    if content == b"":
        return [], True
    parts = content.split(b"\n")
    if parts[-1] == b"":
        return parts[:-1], True
    return parts, False


def apply_hunks(old_content, hunks):
    """Apply hunks strictly.  Returns new content bytes or raises Unparseable."""
    old, had_eol = split_lines(old_content)
    out = []
    pos = 0  # index into old
    for h in hunks:
        start = h.old_start - 1 if h.old_len > 0 else h.old_start
        if start < pos or start > len(old):
            raise Unparseable("hunk out of order")
        out.extend(old[pos:start])
        pos = start
        for op, body in h.lines:
            if op == " ":
                if pos >= len(old) or old[pos] != body:
                    raise Unparseable("context mismatch")
                out.append(body)
                pos += 1
            elif op == "-":
                if pos >= len(old) or old[pos] != body:
                    raise Unparseable("removal mismatch")
                pos += 1
            else:
                out.append(body)
    out.extend(old[pos:])
    eol = had_eol
    if hunks and pos >= len(old):
        eol = not hunks[-1].new_noeol
    data = b"\n".join(out)
    if out and eol:
        data += b"\n"
    return data


def old_changed_lines(hunks):
    """(removed old line numbers, insertion points) for a list of hunks.

    insertion point = (a, b): inserted text sits between old line a (0 if none) and old line b
    (len+1 if none).
    """
    removed = []
    inserts = []
    for h in hunks:
        o = h.old_start if h.old_len > 0 else h.old_start + 1
        run_start = None
        for op, body in h.lines:
            if op == " ":
                if run_start is not None:
                    inserts.append((run_start, o))
                    run_start = None
                o += 1
            elif op == "-":
                if run_start is not None:
                    inserts.append((run_start, o))
                    run_start = None
                removed.append(o)
                o += 1
            else:
                if run_start is None:
                    run_start = o - 1
        if run_start is not None:
            inserts.append((run_start, o))
    return removed, inserts
