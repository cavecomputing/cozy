# Security

## Network access

Cozy is a single-user application. It has no login screen, user accounts, or
authentication.

The default Python and Docker configurations listen only on the local computer.
Do not bind Cozy to a public network interface unless you place an
authentication layer and HTTPS reverse proxy in front of it.

## Private data

The data directory contains private information:

- Chat history
- API keys
- LLM server settings
- Personas
- Character cards
- Lorebooks

API keys are stored as plain text in `data/cozy_chat.db`. Protect the data
directory and do not commit it to Git.

## Reporting a vulnerability

Message me or something I don't know. Probably won't fix it 🤷
