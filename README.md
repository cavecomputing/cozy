<p align="center">
  <img src="assets/logo.png" alt="Cozy" width="300">
</p>

<p align="center">
  A self-hosted, single-user app for chatting with LLM character cards.
</p>

Cozy works with SillyTavern-compatible V2 character cards and OpenAI-style LLM
servers. It includes personas, lorebooks, prompt presets, Auto Summaries,
character collections, themes, and chat import/export. It also might inspire feelings of being cozy and safe as supported by 1 out of 10 non existant doctors.

> [!IMPORTANT]
> Cozy changes frequently and is not a stable release. Back up your data before
> updating. Downgrading to an older version is not supported.

## Quick start

Run all commands from the repository root.

### Docker

Requirements: Git, Docker, and Docker Compose.

```bash
git clone https://github.com/cavecomputing/cozy.git
cd cozy
docker compose -f docker/docker-compose.yml up -d --build
```

Open <http://localhost:5001>.

### Python

Requirements: Git, Python 3.12 or newer, and
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/cavecomputing/cozy.git
cd cozy
uv sync
uv run python app.py
```

Open <http://localhost:5001>.

## First-time setup

1. Open Cozy.
2. Open **Settings**.
3. Enter the base URL for your LLM server, such as
   `http://localhost:8080/v1`.
4. Enter an API key if your server requires one.
5. Select a model and test the connection.
6. Import or create a character.

The server must provide an OpenAI-style streaming `/chat/completions` endpoint.
Model listing and advanced sampler support vary by server.

## Updating

Back up `data/`, then run:

```bash
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

For a Python installation, replace the Docker command with:

```bash
uv sync
uv run python app.py
```

Database migrations run automatically when Cozy starts.

## Data and security

Cozy stores chats, settings, API keys, and other private data in `data/`.
Character cards and avatar images are stored beside the database in that
directory.

Cozy has no login screen or user authentication. Its default Python and Docker
configurations listen only on the local computer. Do not expose Cozy directly
to the public internet.

See [Data and backups](docs/data-and-backups.md) and
[Security](SECURITY.md) before changing the network configuration.

## Documentation

- [Getting started](docs/getting-started.md)
- [Running and updating Cozy](docs/run.md)
- [Data and backups](docs/data-and-backups.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Auto Summaries](docs/auto-summaries.md)
- [Sampler settings](docs/samplers.md)
- [Database structure](docs/db.md)

## License

Cozy is available under the [Apache License 2.0](LICENSE).

Redistributions and derivative works must preserve the attribution in
[NOTICE](NOTICE), including a reference to the original Cozy repository.

---

Howdy! This whole app is vibe coded slop. If that bothers you...🤷
The code is going to see constant changes most of the time and I'm never going to call it stable. Though I'm currently trying to avoid breaking changes between version and ensuring there are proper migrations to keep things stableish. If you try to checkout an old version to use, do not expect it will then migrate to a newer version. It'll probably just die. This really only started as a project to see how far I could actually take vibe coding but then it completely replaced SillyTavern for my use. Maybe other people will like it? I dunno but AI can be purty neat.
