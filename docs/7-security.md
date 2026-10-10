# Security

## Network access

Cozy is intentionally a single-user application, with no user accounts. Unless
you [set a password](#require-a-password), there is no login screen either, and
anything that can reach Cozy can read your chats and use your API key.

The default Python and Docker configurations listen only on the local computer.
Do not listen on a public network interface unless you are only accessing over LAN. If you do, set a password.
Do not make this accessible over the internet unless your remote access method is a VPN. Consider authentication via reverse proxy.

Cozy only accepts changes from its own page. When your browser marks a
request as coming from another website — or from another port on the same
computer — Cozy refuses it, so a page you happen to have open cannot import,
delete or restore anything. That is not a login: without a password, anything
that can reach Cozy directly, such as a script on your network, can still use
it.

## Require a password

Set `COZY_PASSWORD` and every device has to sign in before Cozy shows it
anything. Leave it unset and Cozy works as before, with no sign-in.

Python, on Linux or macOS:

```bash
COZY_PASSWORD='your password' uv run app.py --host 0.0.0.0
```

Windows PowerShell:

```powershell
$env:COZY_PASSWORD = 'your password'
uv run app.py --host 0.0.0.0
```

Docker: create a file named `.env` in the `docker/` folder, next to
`compose.yml`, holding

```text
COZY_PASSWORD='your password'
```

then start Cozy, or restart it, with
`docker compose -f docker/compose.yml up -d`. Keep the single quotes, so a `$`
in the password stays a `$`. Git ignores the file, and it never goes into the
image.

What it covers:

- Everything needs a signed-in device except the sign-in page, Cozy's own
  scripts and stylesheets, and theme stylesheets (the `.css` files in
  `data/themes/` among them), which stay open so the sign-in page can wear the
  theme you picked.
- **Keep this device signed in** keeps a device signed in for 400 days. Without
  it, the browser drops the sign-in when it closes.
- **Settings → About → Sign out** signs this browser out. Changing
  `COZY_PASSWORD` and restarting signs every device out, and is the only way to
  cut off a sign-in cookie someone has copied; do it if a device is lost, or if
  you think someone else has signed in.
- After a wrong password, Cozy refuses every sign-in for a second, so guessing
  is slow. Devices already signed in are not affected, but anything that keeps
  guessing also keeps new devices from signing in until it stops. Use a long
  password all the same.
- If you forget the password, set a new one and restart. There is nothing else
  to reset.

Over plain HTTP the password and the sign-in cookie cross the network
unencrypted, so anything that can watch your network traffic can read them.
Beyond your own network, use a VPN, or a reverse proxy that serves HTTPS. Cozy
marks its cookie Secure when the proxy says the browser used HTTPS with
`X-Forwarded-Proto`. Caddy and Traefik send that header on their own; nginx
needs `proxy_set_header X-Forwarded-Proto $scheme;`.

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
[Data and backups](3-data-and-backups.md).
Because API keys are stored as plain text in the Cozy database, a backup
contains them in plain text too. Keep backup files as private as the data
directory itself.
