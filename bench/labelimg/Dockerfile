# Labeling image: the kit's pinned base, requirements.lock, sqlglot at the pinned commit.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
RUN apt-get update && apt-get install -y --no-install-recommends git patch ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
RUN git clone -q https://github.com/tobymao/sqlglot /clean && cd /clean && git checkout -q eb11aec4be8cb2122afe713a4816662c3f0fb2fa && rm -rf .git/hooks
WORKDIR /clean
