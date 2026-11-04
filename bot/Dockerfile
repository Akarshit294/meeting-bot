FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG PLAYWRIGHT_VERSION=1.64.0

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    SCREEN_SIZE=1280x720

RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
 && if [ "$TARGETARCH" = "arm64" ]; then playwright install --with-deps chromium; \
    else playwright install --with-deps chrome; fi \
 && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini xvfb x11vnc novnc websockify \
 && rm -rf /var/lib/apt/lists/* \
 && cd /usr/share/novnc \
 && echo '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale&reconnect=true&view_only=true">' \
    > index.html \
 && echo '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale&reconnect=true">' \
    > control.html

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN useradd --create-home bot \
 && mkdir -p /output /mouse /tmp/.X11-unix \
 && chmod 1777 /tmp/.X11-unix \
 && chown bot /output /mouse

COPY --chmod=755 entrypoint.sh meet_bot.py human_mouse.py live_session.py /app/
COPY recorder.html meet-recorder.html /app/

USER bot
WORKDIR /app
EXPOSE 6080 8080
ENTRYPOINT ["tini", "--", "/app/entrypoint.sh"]
