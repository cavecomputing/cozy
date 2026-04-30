FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu libjpeg-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${GID}" cozy \
 && useradd -m -u "${UID}" -g cozy cozy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/characters /data/personas /data/themes \
 && chown -R cozy:cozy /app /data

ENV COZY_DATA_DIR=/data

EXPOSE 5001

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--worker-class", "gthread", "--threads", "4", "app:app"]
