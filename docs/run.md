# Running Cozy

Run every command on this page from the repository root.

## Docker

### Requirements

- Git
- Docker with the `docker compose` command

### Start

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

Open <http://localhost:5001>.

The first build may take several minutes. `-d` leaves Cozy running in the
background.

### Check status

```bash
docker compose -f docker/docker-compose.yml ps
```

### View logs

```bash
docker compose -f docker/docker-compose.yml logs -f
```

Press `Ctrl+C` to stop viewing logs. Cozy continues running.

### Stop

```bash
docker compose -f docker/docker-compose.yml down
```

This removes the container but does not delete `data/`.

### Open a container shell

```bash
docker compose -f docker/docker-compose.yml exec cozypub /bin/sh
```

### Use a different port

The default mapping is:

```yaml
ports:
  - "127.0.0.1:5001:5001"
```

Change the first `5001` in `docker/docker-compose.yml` to change the host port:

```yaml
ports:
  - "127.0.0.1:8080:5001"
```

After restarting Cozy, open `http://localhost:8080`.

### Custom UID and GID on Linux

The container uses UID and GID 1000 by default. On Linux, build it with your
account IDs if `data/` has permission problems:

```bash
docker compose -f docker/docker-compose.yml build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
docker compose -f docker/docker-compose.yml up -d
```

## Python

### Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

### Install and start

```bash
uv sync
uv run python app.py
```

Open <http://localhost:5001>.

This runs Flask's development server with automatic reload. Use the Docker setup
for a persistent installation.

### Use a different data directory

Linux or macOS:

```bash
COZY_DATA_DIR=/path/to/cozy-data uv run python app.py
```

PowerShell:

```powershell
$env:COZY_DATA_DIR = "C:\path\to\cozy-data"
uv run python app.py
```

Cozy creates the directory and its required subdirectories when it starts.

## Updating

Stop Cozy and [back up the data directory](data-and-backups.md), then run:

### Docker

```bash
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

### Python

```bash
git pull
uv sync
uv run python app.py
```

Database migrations run automatically during startup.

## Run tests

```bash
uv sync --dev
uv run pytest
```

Node.js must be available on `PATH` for the frontend request-builder tests.
Without Node.js, those tests are skipped.
