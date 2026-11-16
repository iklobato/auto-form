# auto-form in a container: profile.yaml + resume PDF in, job forms out.
# Build:  docker build -t auto-form .
# Run:    docker run --rm -v "$PWD/data:/data" -e OPENROUTER_TOKEN auto-form --all
# /data must hold profile.yaml and resume.pdf; output goes to /data/out.
# Nothing is submitted unless you add --send.
FROM python:3.13-slim

# Debian chromium: browser_use finds it at /usr/bin/chromium
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    IN_DOCKER=true \
    AUTO_FORM_DATA=/data/out \
    RESUME_PATH=/data/resume.pdf

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY fill.py ./
COPY prompts ./prompts
COPY portals ./portals

# Non-root; browser_use turns the Chrome sandbox off inside Docker (IN_DOCKER)
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app

ENTRYPOINT ["python", "/app/fill.py", "--profile", "/data/profile.yaml", \
            "--systems", "/app/portals/no-login.yaml", "--headless"]
CMD ["--all"]
