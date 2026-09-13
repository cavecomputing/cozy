# Running Cozy

Run every command on this page from the top folder of the Cozy repository
(the folder that contains `app.py`).

Everyday use on your own computer is covered by **Quick start** in the
[README](../README.md). This page is for the other cases: running Cozy
with Docker, on a server, on a different port or address, storing data
somewhere else, updating, and running the tests.

## Docker

Use Docker when you want Cozy to keep running in the background, for
example on a server.

### What you need

- Git
- Docker, with the `docker compose` command available

### Start

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

Then open <http://localhost:5001> in your browser.

The first start can take several minutes, because Docker has to build the
app image. The `-d` flag means "run in the background". Cozy keeps running
after you close the terminal.

### Check that it is running

```bash
docker compose -f docker/docker-compose.yml ps
```

### Read the logs

```bash
docker compose -f docker/docker-compose.yml logs -f
```

New log lines appear as they happen. Press `Ctrl+C` to stop watching.
Cozy keeps running.

### Stop

```bash
docker compose -f docker/docker-compose.yml down
```

This removes the container. It does not delete your `data/` folder. Your
chats, characters, and settings stay where they are.

### Open a shell inside the container

You rarely need this. It opens a command line inside the running
container:

```bash
docker compose -f docker/docker-compose.yml exec cozypub /bin/sh
```

### Use a different port

The default mapping in `docker/docker-compose.yml` is:

```yaml
ports:
  - "127.0.0.1:5001:5001"
```

This has three parts. The last `5001` is the port inside the container;
leave it alone. The first `5001` is the port on your computer; change this
one to use a different port. The `127.0.0.1` part means only programs on
the same computer can connect.

To use port 8080 instead, change the line to:

```yaml
ports:
  - "127.0.0.1:8080:5001"
```

Restart Cozy, then open `http://localhost:8080`.

To let other machines on your network connect, remove the `127.0.0.1:`
part:

```yaml
ports:
  - "5001:5001"
```

Warning: Cozy has no login screen. Anyone who can reach the address can
read your chats and use your API key. Only open it to a network you trust,
or put it behind a reverse proxy that asks for a password. See
[Security](SECURITY.md).

### File permissions on Linux

Inside the container, Cozy saves files as user 1000 and group 1000. On
Linux, if your account has different IDs, Cozy cannot write to `data/`
and fails. Build the image with your own IDs instead:

```bash
docker compose -f docker/docker-compose.yml build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
docker compose -f docker/docker-compose.yml up -d
```

## Python

Use this when you want to run Cozy directly, without Docker.

### What you need

- Python 3.12 or newer. Check with `python3 --version`.
- [uv](https://docs.astral.sh/uv/). Check with `uv --version`.

### Install and start

```bash
uv sync
uv run app.py
```

`uv sync` downloads the libraries Cozy needs. `uv run app.py` starts Cozy.
Then open <http://localhost:5001> in your browser.

This runs Flask's development server. It restarts itself when program
files change. It stops when you press `Ctrl+C` or close the terminal. For
something that stays running on its own, use the Docker setup above.

### Use a different address or port

By default Cozy listens on `127.0.0.1:5001`. That means port 5001, and
only programs on the same computer can connect.

A different port:

```bash
uv run app.py --port 8080
```

Then open `http://localhost:8080`.

Let other machines on your network connect:

```bash
uv run app.py --host 0.0.0.0 --port 8080
```

Then open `http://<this computer's network address>:8080` on the other
machine.

Warning: Cozy has no login screen. Anyone who can reach the address can
read your chats and use your API key. Only do this on a network you
trust, or behind a reverse proxy that asks for a password. See
[Security](SECURITY.md).

These `--host` and `--port` options work for the Python setup only. Under
Docker, change the port mapping instead — see [Use a different
port](#use-a-different-port) above.

### Use a different data directory

Cozy stores everything in the `data/` folder inside the repository by
default. To store it somewhere else, set the `COZY_DATA_DIR` environment
variable before starting. Cozy creates the folder and everything it needs
inside it when it starts.

Linux or macOS:

```bash
COZY_DATA_DIR=/path/to/cozy-data uv run app.py
```

Windows PowerShell:

```powershell
$env:COZY_DATA_DIR = "C:\path\to\cozy-data"
uv run app.py
```

## Updating

1. Stop Cozy. For Docker, run the [Stop](#stop) command above. For
   Python, press `Ctrl+C` in the terminal where it is running.
2. Copy the `data/` directory (or your custom data directory) somewhere
   safe. Going back to an older version afterwards is not supported. →
   [Data and backups](data-and-backups.md).
3. Run the commands for your setup.

Docker:

```bash
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

Python:

```bash
git pull
uv sync
uv run app.py
```

When Cozy starts, it updates its database automatically.

## Run the tests

You only need this if you changed the code and want to check that it
still works.

```bash
uv sync --dev
uv run pytest
```

Node.js must be installed and on `PATH` for the tests that check Cozy's
frontend JavaScript — currently eleven files, covering the request
builder, the regex engine and bundled regex presets, dialogue matching,
avatars, summaries, thinking blocks, the send flow, stop-mid-reply
handling, storage stats, and the context meter.

Without Node.js those tests are **skipped, not failed**. A run that
reports success on a machine without Node.js has tested none of that
JavaScript. Look at the summary line for skips before trusting a pass.

