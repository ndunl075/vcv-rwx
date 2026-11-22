# The base image digest is the one the kit labeled in: its Python and SQLite decide which tests fail.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS src
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY tools/fetch_sqlglot.py /tmp/fetch_sqlglot.py
RUN python /tmp/fetch_sqlglot.py /opt/sqlglot \
 && python -m compileall -q /opt/sqlglot/sqlglot /opt/sqlglot/tests /opt/sqlglot/sqlglotc \
 && chmod -R a+rX /opt/sqlglot

# Test dependencies (same pins as the kit's requirements.lock): only needed to run the suite.
FROM src AS deps
COPY requirements-run.txt /tmp/requirements-run.txt
RUN pip install -r /tmp/requirements-run.txt

# Development image for building the maps (tools/ use only the standard library).
FROM deps AS dev
RUN pip install pytest-xdist==3.8.0 execnet==2.1.2

# The submission image.
FROM src AS run
COPY selector /app/selector
COPY maps /app/maps
RUN chmod -R a+rX /app
WORKDIR /app
CMD ["python", "-m", "selector"]
