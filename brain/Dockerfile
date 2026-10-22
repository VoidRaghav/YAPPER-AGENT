FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

WORKDIR /app/brain
COPY brain/pyproject.toml brain/uv.lock ./
RUN uv sync --frozen --no-dev --extra all

COPY brain/ ./
COPY assets /app/assets
COPY packs /app/packs

ENV PATH="/app/brain/.venv/bin:$PATH" \
    DATA_DIR=/data \
    MEME_TEMPLATES_DIR=/app/assets/meme_templates

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
