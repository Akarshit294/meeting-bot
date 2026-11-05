# syntax=docker/dockerfile:1.7
# The test kit: fake meeting page + test receiver (tests only). Build context: the repo root.
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
WORKDIR /kit
COPY tests/testkit/requirements.txt /kit/requirements.txt
RUN pip install --no-cache-dir -r /kit/requirements.txt
COPY tests/testkit/ /kit/
EXPOSE 9000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000", "--log-level", "warning"]
