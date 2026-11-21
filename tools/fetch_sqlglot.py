"""Download sqlglot at the pinned commit from GitHub and unpack it to a directory.

Usage: fetch_sqlglot.py <dest>
The tarball of a commit is an immutable object, so the build is reproducible.
"""
import io
import sys
import tarfile
import urllib.request

COMMIT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
URL = f"https://codeload.github.com/tobymao/sqlglot/tar.gz/{COMMIT}"


def main() -> None:
    dest = sys.argv[1]
    with urllib.request.urlopen(URL, timeout=120) as r:
        data = r.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        prefix = f"sqlglot-{COMMIT}/"
        for m in tf.getmembers():
            if not m.name.startswith(prefix):
                continue
            m.name = m.name[len(prefix):]
            if not m.name:
                continue
            tf.extract(m, dest, filter="data")


if __name__ == "__main__":
    main()
