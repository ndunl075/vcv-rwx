# The base image digest is the one the kit labeled in: its Python and SQLite decide which tests fail.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# The environment the labels were made in (PyPI only).
COPY kit/requirements.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock

# sqlglot at the pinned commit (GitHub archive of that commit), with bytecode for its modules and
# pytest's rewritten bytecode for its test modules, so that no run has to compile unchanged files.
COPY tools/fetch_sqlglot.py /tmp/fetch_sqlglot.py
RUN python /tmp/fetch_sqlglot.py /opt/sqlglot \
 && cd /opt/sqlglot \
 && python -m compileall -q sqlglot tests \
 && PYTHONPATH=/opt/sqlglot SKIP_INTEGRATION=0 python -m pytest tests --collect-only -q -p no:cacheprovider > /tmp/collect.txt \
 && tail -1 /tmp/collect.txt \
 && chmod -R a+rX /opt/sqlglot

COPY selector /app/selector
COPY maps /app/maps
RUN python -m compileall -q /app/selector && chmod -R a+rX /app
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "selector"]
