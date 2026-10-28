# Official Playwright image: Chromium + its system libraries preinstalled.
# The tag must match the playwright version in uv.lock, or the driver can't find its browser.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Dependencies first so code-only changes reuse the cached layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev

COPY agent.py mail.py report.py scraper.py ./

# Secrets (AGENTMAIL_API_KEY, AGENTMAIL_INBOX, ANTHROPIC_API_KEY) come from the host's environment, never the image.
CMD ["uv", "run", "--frozen", "--no-sync", "python", "agent.py"]
