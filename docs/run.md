# Running Cozy

## With Python

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### Install dependencies

```bash
uv sync            # runtime deps
uv sync --dev      # include test deps (pytest)
```

### Start the app

```bash
uv run python app.py
```

This starts Flask's dev server on **port 5001** with auto-reload. Open `http://localhost:5001` in your browser.

All data is stored in the `data/` directory relative to the project root by default. This includes the SQLite database, character card PNGs, persona avatars, and user themes.

### Custom data directory

Set the `COZY_DATA_DIR` environment variable to store data elsewhere:

```bash
COZY_DATA_DIR=/path/to/my/data python app.py
```

The directory will be created automatically if it doesn't exist. Subdirectories (`characters/`, `personas/`, `themes/`) are also created on startup.

## With Docker

### Build and run

```bash
cd docker
docker compose -f docker/docker-compose.yml up -d --build
```

The app is available at `http://localhost`. Data is stored in `data/` at the project root, mounted into the container at `/data`.

### Run in background

```bash
cd docker
docker compose up --build -d
```

### Stop

```bash
cd docker
docker compose down
```

### Shell into a running container

```bash
cd docker
docker compose exec cozypub /bin/bash
```

### Custom UID/GID

The container runs as user `cozy` (UID/GID 1000 by default). To match your host user:

```bash
cd docker
docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
docker compose up
```

### Port mapping

The default maps host port `80` to container port `5001`. Edit `docker/docker-compose.yml` to change:

```yaml
ports:
  - "8080:5001"  # change 8080 to your preferred port
```

## Updating

### Python

Pull the latest code and reinstall dependencies:

```bash
git pull
uv sync
```

Then restart the app. Current schema setup and seed data run automatically on startup.

### Docker

Rebuild the image and restart:

```bash
cd docker
docker compose up --build -d
```

The `data/` volume is mounted from the host, so your data persists across rebuilds.

## Data directory layout

```
data/
  cozy_chat.db        SQLite database (chats, settings, personas, etc.)
  characters/          Character card PNGs with embedded V2 card data
  personas/            Persona avatar images
  themes/              User-added theme CSS files (merged with built-in themes)
```

Both Python and Docker modes use the same `data/` directory structure, so you can switch between them freely.
