# Security

## Network access

Cozy is intentionally a single-user application. There is no login screen, no user accounts, and no authentication.

The default Python and Docker configurations listen only on the local computer.
Do not listen on a public network interface unless you are only accessing over LAN. If you do, consider an authentication layer with a reverse proxy.
Do not make this accessible over the internet unless your remote access method is a VPN. Consider authentication via reverse proxy.

## Private data

Cozy stores chats, settings, API keys, and other private data in `data/`, under
either the Docker or the uv setup. Character cards and avatar images are stored
beside the database in that same directory.

The data directory contains private information:

- Chat history
- API keys
- LLM server settings
- Personas
- Character cards
- Lorebooks

API keys are stored as plain text in `data/cozy_chat.db`. Protect the data
directory and do not commit it to Git.

Settings → About → Storage has a **Backup** menu that downloads the whole data
directory as a zip and restores one on top of it, so a backup does not mean
stopping Cozy and copying folders. See
[Data and backups](data-and-backups.md).
Note: since API keys are stored as plain text in the Cozy database, a backup also inherently contains the same information in pain text.
