# Running Cozy

## With Python

### Prerequisites

- Python 3.10+
- pip

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start the app

```bash
python app.py
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
docker compose up --build
```

The app is available at `http://localhost:9002`. Data is stored in `data/` at the project root, mounted into the container at `/data`.

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

The default maps host port `9002` to container port `5001`. Edit `docker/docker-compose.yml` to change:

```yaml
ports:
  - "8080:5001"  # change 8080 to your preferred port
```

## Updating

### Python

Pull the latest code and reinstall dependencies:

```bash
git pull
pip install -r requirements.txt
```

Then restart the app. Database migrations run automatically on startup.

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
