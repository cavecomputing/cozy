FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu libjpeg-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${GID}" cozy \
 && useradd -m -u "${UID}" -g cozy cozy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN mkdir -p /data/characters /data/personas /data/themes \
 && chown -R cozy:cozy /app /data

ENV COZY_DATA_DIR=/data
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 5001

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# --timeout 0 disables the worker timeout so long-running SSE streams from slow
# local backends aren't killed mid-response (gthread's default is 30s). See #6.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--worker-class", "gthread", "--threads", "4", "--timeout", "0", "app:app"]
