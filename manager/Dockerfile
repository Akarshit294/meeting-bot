# syntax=docker/dockerfile:1.7
# The manager: the Recall-compatible API. Build context: the repo root.
# NET=proxy is only for the overnight build VM (see bot/Dockerfile); the Mac uses the default.
ARG NET=direct
# The Python base, pinned (why, and how to update it: bot/Dockerfile). Keep the three Dockerfiles the same.
ARG BASE=python:3.12-slim-bookworm@sha256:da54eb86849fe19be7584d8b3f291fcf0c53c196760ab3d70dc6382f681ef9c0

FROM ${BASE} AS net-direct

FROM ${BASE} AS net-proxy
COPY --from=proxyca agent-proxy-ca.crt /usr/local/share/ca-certificates/build-proxy.crt
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt

FROM net-${NET}
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY manager/requirements.txt /app/manager/requirements.txt
RUN pip install --no-cache-dir -r /app/manager/requirements.txt
COPY manager /app/manager
EXPOSE 8090
CMD ["python", "-m", "manager.app"]
