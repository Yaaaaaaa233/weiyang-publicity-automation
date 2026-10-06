FROM selenium/standalone-chromium:4.48.0-20260905
USER root
RUN apt-get update && apt-get install -y --no-install-recommends xclip \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /home/seluser/profile /opt/artifacts /opt/project \
    && chown -R seluser:seluser /home/seluser/profile /opt/artifacts /opt/project
COPY --chown=seluser:seluser scripts/browser.py /opt/project/browser.py
COPY --chown=seluser:seluser fixtures/ /opt/project/fixtures/
USER seluser
