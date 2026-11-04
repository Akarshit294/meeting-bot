# syntax=docker/dockerfile:1.7
# The test kit: fake meeting page + test receiver (tests only). Build context: the repo root.
ARG NET=direct

FROM python:3.12-slim-bookworm AS net-direct

FROM python:3.12-slim-bookworm AS net-proxy
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
