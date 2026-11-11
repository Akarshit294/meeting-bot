# syntax=docker/dockerfile:1.7
# The bot image: one container per meeting.
#
# NET picks how the build reaches the internet:
#   direct (default, the Mac): nothing special.
#   proxy (only the overnight build VM, which reaches the internet through an HTTPS proxy):
#         adds that proxy's CA certificate. Needs `--build-context proxyca=<dir with agent-proxy-ca.crt>`,
#         `--network host` and the https_proxy build arg. The Mac never builds this stage.
ARG NET=direct
# The Python base, pinned to one exact build of python:3.12-slim-bookworm (the one Docker Hub had on
# 2026-11-05). Docker Hub replaces what that name points to every few weeks; unpinned, the first build after
# such a change rebuilt everything (here: Chrome and all, 7 minutes) instead of taking seconds.
# The same line is in bot/, manager/ and tests/testkit/. To move to a newer base on purpose, put the top
# "Digest:" of `docker buildx imagetools inspect python:3.12-slim-bookworm` in all three.
ARG BASE=python:3.12-slim-bookworm@sha256:da54eb86849fe19be7584d8b3f291fcf0c53c196760ab3d70dc6382f681ef9c0

FROM ${BASE} AS net-direct

FROM ${BASE} AS net-proxy
COPY --from=proxyca agent-proxy-ca.crt /usr/local/share/ca-certificates/build-proxy.crt
RUN sed -i 's#http://deb.debian.org#https://deb.debian.org#g' /etc/apt/sources.list.d/debian.sources \
 && update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

FROM net-${NET}

ARG TARGETARCH
ARG PLAYWRIGHT_VERSION=1.64.0

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    SCREEN_SIZE=1280x720

# Both browsers (Meet, and our own pages) are Google Chrome, as in the proven join. Chrome has no arm64
# Linux build, so there Playwright's Chromium is used instead.
# The build installs Google's newest stable Chrome, then keeps it in the build cache: later builds keep that
# Chrome however old it gets. Change CHROME_REFRESH (any new value, e.g. today's date) to install the newest
# one; that build takes about 7 minutes. Do it at least every few weeks: Meet refuses a browser that looks
# odd, and a Chrome left behind by real people's updates is one more oddity. On 2026-11-11 Meet refused the
# container (Chrome 157.0.8103.38 from the 2026-11-05 build) nearly every time; with the newest Chrome, the
# fonts below and the time zone (BROWSER_TZ, bot/meet_bot.py) it got in 3 of 3.
# Why: docs/why-meet-refused-the-bot.md. Check what the browser shows: scripts/browser_check.py.
ARG CHROME_REFRESH=2026-11-11
RUN echo "Chrome refresh: ${CHROME_REFRESH}" \
 && pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
 && if [ "$TARGETARCH" = "arm64" ]; then playwright install --with-deps chromium; \
    else playwright install --with-deps chrome; fi \
 && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini xvfb x11vnc novnc websockify \
      pulseaudio pulseaudio-utils x11-utils ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && cd /usr/share/novnc \
 && echo '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=true">' \
    > index.html \
 && echo '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale&reconnect=true">' \
    > control.html

# The fonts a Linux desktop has. Without them Chrome had 5 (the Liberation ones), and websites can count
# fonts: a computer with almost no fonts looks like a server, not a person (2026-11-11, see above).
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-noto-core fonts-noto-mono \
      fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN useradd --create-home bot \
 && mkdir -p /output /mouse /tmp/.X11-unix \
 && chmod 1777 /tmp/.X11-unix \
 && chown bot /output /mouse

# The owner's recorded mouse movements and typing rhythm (the proven join replays them).
# `docker run -v "$PWD/mouse:/mouse"` still replaces them, as before.
COPY --chown=bot mouse/ /mouse/
COPY --chmod=755 entrypoint.sh /app/
COPY *.py /app/
COPY recorder.html meet-recorder.html pulse.pa /app/

USER bot
WORKDIR /app
EXPOSE 6080 8080
ENTRYPOINT ["tini", "--", "/app/entrypoint.sh"]
