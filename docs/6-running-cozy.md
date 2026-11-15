# Running Cozy

This guide explains how to keep Cozy running, reach it from other devices,
protect it with a password, back up your data, and update.

You do not need it to try Cozy on your own computer. **Quick start** in the
[README](../README.md#quick-start) covers that.

Run every command in this guide from the top folder of the Cozy repository.
That is the folder that contains `app.py`.

Go to the part you need:

- [Run Cozy with Docker](#run-cozy-with-docker): Cozy keeps running in the
  background, for example on a server.
- [Run Cozy with Python](#run-cozy-with-python): Cozy runs in a terminal and
  stops when you close it.
- [Let other devices connect](#let-other-devices-connect): use Cozy from your
  phone or another computer.
- [Require a password](#require-a-password)
- [Where your data is](#where-your-data-is)
- [Back up and restore](#back-up-and-restore)
- [Update Cozy](#update-cozy)
- [Go back after an update](#go-back-after-an-update)
- [What is stored as plain text](#what-is-stored-as-plain-text)
- [Run the tests](#run-the-tests): only if you change the code.

## Run Cozy with Docker

Use Docker when Cozy should keep running in the background, for example on a
server.

You need Git, and Docker with the `docker compose` command.

**Start Cozy:**

```bash
docker compose -f docker/compose.yml up -d --build
```

Then open <http://localhost:5000> in your browser.

The first start can take several minutes, because Docker builds the app
first. `-d` means "run in the background". Cozy keeps running after you close
the terminal.

**Check that it is running:**

```bash
docker compose -f docker/compose.yml ps
```

**Read the logs:**

```bash
docker compose -f docker/compose.yml logs -f
```

New log lines appear as they happen. Press `Ctrl+C` to stop watching. Cozy
keeps running.

**Stop Cozy:**

```bash
docker compose -f docker/compose.yml down
```

This removes the container. It does not delete your `data/` folder. Your
chats, characters and settings stay where they are.

**Open a command line inside the container.** You rarely need this:

```bash
docker compose -f docker/compose.yml exec cozypub /bin/sh
```

### Use a different port

The port is set in `docker/compose.yml`. The default is:

```yaml
ports:
  - "127.0.0.1:5000:5000"
```

The line has three parts:

| Part | What it means | Change it? |
|---|---|---|
| `127.0.0.1` | Only programs on this computer can connect. | Only to [let other devices connect](#let-other-devices-connect). |
| The first `5000` | The port on your computer. | Yes. This is the port you open in the browser. |
| The last `5000` | The port inside the container. | No. |

To use port 8080:

1. Change the line to `- "127.0.0.1:8080:5000"`.
2. Run `docker compose -f docker/compose.yml up -d`.
3. Open `http://localhost:8080`.

### File permissions on Linux

Inside the container, Cozy runs as user 1000 and group 1000. Each time it
starts, it makes that user the owner of everything in `data/`.

On Linux, if your own account has different IDs, your account may then be
unable to change or copy the files in `data/`. To fix this, build the app
with your own IDs:

```bash
docker compose -f docker/compose.yml down
docker compose -f docker/compose.yml build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
docker compose -f docker/compose.yml up -d
```

## Run Cozy with Python

Use this to run Cozy directly, without Docker.

You need [uv](https://docs.astral.sh/uv/). Check with `uv --version`. You do
not need to install Python yourself. The first `uv sync` downloads the version
Cozy uses.

**Install and start:**

```bash
uv sync
uv run app.py
```

`uv sync` downloads the libraries Cozy needs. `uv run app.py` starts Cozy.
Then open <http://localhost:5000> in your browser.

Cozy stops when you press `Ctrl+C` or close the terminal. To keep it running
without a terminal, [run Cozy with Docker](#run-cozy-with-docker).

### Use a different address or port

By default Cozy listens on `127.0.0.1:5000`. That means port 5000, and only
programs on this computer can connect.

To use port 8080:

```bash
uv run app.py --port 8080
```

Then open `http://localhost:8080`.

`--host` sets the address. See
[Let other devices connect](#let-other-devices-connect).

`--host` and `--port` work for the Python setup only. Under Docker,
[change the port in `docker/compose.yml`](#use-a-different-port).

## Let other devices connect

By default, only the computer that runs Cozy can open it. To use Cozy from
your phone or another computer on the same network, do these three steps.

1. **Choose a password.** Without one, anything that can reach Cozy can read
   your chats and use your API key.
2. **Start Cozy with the password, listening to the network.**

   Python, on Linux or macOS:

   ```bash
   COZY_PASSWORD='your password' uv run app.py --host 0.0.0.0
   ```

   Python, in Windows PowerShell:

   ```powershell
   $env:COZY_PASSWORD = 'your password'
   uv run app.py --host 0.0.0.0
   ```

   Docker: put the password in `docker/.env`, as described in
   [Require a password](#require-a-password). Then, in `docker/compose.yml`,
   remove `127.0.0.1:` from the ports line, so it reads `- "5000:5000"`. Then
   run `docker compose -f docker/compose.yml up -d`.
3. **On the other device, open** `http://<address>:5000`, where `<address>` is
   the network address of the computer that runs Cozy. For example
   `http://192.168.1.20:5000`. The sign-in page appears. Type the password.

To put Cozy on a phone's Home Screen, see
[Mobile use](../README.md#mobile-use) in the README.

Rules for doing this safely:

- **Only do this on a network you trust**, such as your home network.
- **Do not open Cozy to the internet directly.** To reach it from outside your
  home network, use a VPN. The other option is a reverse proxy that serves
  HTTPS.
- **Cozy is for one person.** It has no user accounts. Everyone who signs in
  sees the same chats and settings.
- **Without HTTPS, the password is not encrypted on the network.** See
  [Require a password](#require-a-password).

One protection is always on, with or without a password: Cozy accepts changes
only from its own page. If a different website that is open in your browser
tries to import, delete or restore something in Cozy, Cozy refuses. This does
not replace a password. A program on your network can still reach Cozy
directly.

## Require a password

Set the `COZY_PASSWORD` environment variable, and every device must sign in
before Cozy shows it anything. If you do not set it, Cozy has no sign-in page.

Python, on Linux or macOS:

```bash
COZY_PASSWORD='your password' uv run app.py --host 0.0.0.0
```

Python, in Windows PowerShell:

```powershell
$env:COZY_PASSWORD = 'your password'
uv run app.py --host 0.0.0.0
```

Docker:

1. Create a file named `.env` in the `docker/` folder, next to `compose.yml`.
2. Put this one line in it:

   ```text
   COZY_PASSWORD='your password'
   ```

   Keep the single quotes. They make a `$` in the password stay a `$`.
3. Start or restart Cozy with `docker compose -f docker/compose.yml up -d`.

Git ignores the `.env` file, and it is not copied into the Docker image.

How the password works:

| Question | Answer |
|---|---|
| How long does a device stay signed in? | With **Keep this device signed in** checked on the sign-in page: 400 days. Without it: until the browser is closed. |
| How do I sign out? | **Settings → About → Sign out** signs out the browser you are using. |
| How do I sign out every device? | Change `COZY_PASSWORD` and restart Cozy. Do this if a device is lost, or if you think someone else has signed in. |
| What if I forget the password? | Set a new `COZY_PASSWORD` and restart Cozy. There is nothing else to reset. |
| What happens after a wrong password? | Cozy refuses every sign-in for one second. This makes guessing slow. Devices that are already signed in are not affected. Use a long password anyway. |
| What can be opened without signing in? | Only the sign-in page, Cozy's own scripts and stylesheets, and theme stylesheets, including the `.css` files in `data/themes/`. Theme files are open so that the sign-in page can use your theme. |

**Without HTTPS, the password is not encrypted.** Over plain `http://`, the
password and the sign-in cookie cross the network as readable text. Anything
that can watch your network traffic can read them. To reach Cozy from outside
your own network, use a VPN, or a reverse proxy that serves HTTPS.

Behind a reverse proxy, Cozy marks its cookie as HTTPS-only when the proxy
sends the `X-Forwarded-Proto` header. Caddy and Traefik send it by themselves.
nginx needs `proxy_set_header X-Forwarded-Proto $scheme;`.

## Where your data is

Everything Cozy saves is in one folder: `data/`, inside the repository.
Docker uses the same folder.

| In `data/` | What it holds |
|---|---|
| `cozy_chat.db` | The database: chats, settings, API keys, prompt presets, personas, lorebooks, and the list of characters. |
| `characters/` | One `.png` file for each character. The character's text is stored inside the image. |
| `personas/` | Persona pictures. |
| `themes/` | Themes you added. See [Themes](3-user-themes.md). |
| `thumbs/` | Small copies of pictures, made by Cozy to load faster. You can delete this folder at any time, also while Cozy is running. Cozy makes the copies again. |
| `cozy_chat.before-update.db` | A copy of the database from just before the last update that changed it. See [Go back after an update](#go-back-after-an-update). |

**Settings → About → Storage** shows how much space each of these uses.
**Your data** is the total of everything a backup includes. The thumbnail
cache is listed separately and is not part of that total.

### Store the data somewhere else

Python: set the `COZY_DATA_DIR` environment variable before you start Cozy.
Cozy creates the folder if it does not exist.

Linux or macOS:

```bash
COZY_DATA_DIR=/path/to/cozy-data uv run app.py
```

Windows PowerShell:

```powershell
$env:COZY_DATA_DIR = "C:\path\to\cozy-data"
uv run app.py
```

Docker: in `docker/compose.yml`, change the left side of the volumes line to
the folder you want. Write the full path:

```yaml
volumes:
  - /path/to/cozy-data:/data
```

Then run `docker compose -f docker/compose.yml up -d`.

## Back up and restore

The quickest backup is the one inside Cozy. You do not need to stop Cozy.

### Download a backup

1. Open **Settings → About**.
2. In the **Storage** card, click **Backup**, then **Download backup…**.

Your browser downloads one file named `cozy-backup-<date>.zip`. It contains
the whole data folder except the thumbnail cache.

- The backup is correct even if a reply is being written while it downloads.
- The backup contains your API keys as plain text. Keep the file private.
- The download cannot be paused and resumed. If it is interrupted, start it
  again.

### Restore a backup

**Restoring deletes everything Cozy has now** and replaces it with the
contents of the backup: chats, characters, personas, themes, settings and API
keys. This cannot be undone.

1. Open **Settings → About**.
2. In the **Storage** card, click **Backup**, then **Restore from backup…**.
3. Choose the `.zip` file.
4. Cozy asks you to confirm. Click **Replace everything**.

The page reloads when the restore is finished. If a password is set, devices
stay signed in.

What Cozy checks, and what it does not:

- **A backup from an older version of Cozy works.** Cozy updates its database
  during the restore, the same way it does when it starts after an update.
  The one exception is a backup made before database version 21. Cozy refuses
  it and changes nothing. See
  [The database is too old](7-troubleshooting.md#the-database-is-too-old).
- **A backup from a newer version of Cozy is refused.** Update Cozy first.
- **The disk needs room for both.** Cozy deletes the old data only after the
  whole backup is in place. If there is not enough free space, Cozy refuses
  before it changes anything.
- **A restore that fails puts the old data back**, and says so. If Cozy itself
  is stopped in the middle of a restore, the old data is in a folder whose
  name starts with `.restore-`, inside the data folder.
- **Only restore backups you made yourself.** Cozy checks that the file is a
  Cozy backup and that its database opens. It does not check the contents any
  further.

### Back up by hand

1. Stop Cozy. Docker: `docker compose -f docker/compose.yml down`. Python:
   press `Ctrl+C` in the terminal that runs Cozy.
2. Copy the whole `data/` folder to a safe place.

Copy the whole folder, not only `cozy_chat.db`. The database can also use two
more files next to it, `cozy_chat.db-wal` and `cozy_chat.db-shm`.

### Restore by hand

1. Stop Cozy.
2. Rename the current `data/` folder, for example to `data-old/`.
3. Put the backed-up `data/` folder in its place.
4. Start Cozy.

Keep `data-old/` until you have seen that the restored copy works.

## Update Cozy

1. Stop Cozy. Docker: `docker compose -f docker/compose.yml down`. Python:
   press `Ctrl+C` in the terminal that runs Cozy.
2. [Back up your data](#back-up-and-restore). An older version of Cozy cannot
   read data that a newer version has changed.
3. Run the commands for your setup.

   Docker:

   ```bash
   git pull
   docker compose -f docker/compose.yml up -d --build
   ```

   Python:

   ```bash
   git pull
   uv sync
   uv run app.py
   ```

When Cozy starts, it updates its database by itself. You do not run anything
else. The one exception is a database older than version 21. Cozy does not
start on it. See
[The database is too old](7-troubleshooting.md#the-database-is-too-old).

A Cozy page that was left open, and the app installed on a phone, load the new
version by themselves the next time you return to them.

If the update does not work, see
[An update fails](7-troubleshooting.md#an-update-fails).

## Go back after an update

An update can change the database, and an older version of Cozy cannot read
the changed database. For this reason, when Cozy starts after such an update,
it first copies the database to `cozy_chat.before-update.db`, next to
`cozy_chat.db`.

To return to the version you ran before the update:

1. Stop Cozy.
2. Move `cozy_chat.db` to another folder. If `cozy_chat.db-wal` and
   `cozy_chat.db-shm` exist, move them too.
3. Rename `cozy_chat.before-update.db` to `cozy_chat.db`.
4. Check out the older version of Cozy with Git, and start it.

Limits of this copy:

- **It reaches back one update only.** The next update that changes the
  database replaces the copy.
- **Chats and settings from after the update are not in it.** They stay in
  the file you moved in step 2.
- **Character cards are not in it.** They are separate files, and stay as
  they are now.
- **It is not part of a downloaded backup**, and it does not count toward
  **Your data**.

A backup you made before the update is the better way back. This copy is for
when you did not make one.

## What is stored as plain text

Nothing in the data folder is encrypted. Anyone who can read the folder, or a
backup of it, can read:

- your API keys
- your chats
- your personas, character cards and lorebooks
- the addresses of your AI servers

So:

- Keep the data folder and every backup file private.
- Do not commit the data folder to Git. Cozy's `.gitignore` already leaves
  `data/` out.
- Inside Cozy, the full API key is not shown again after you type it. That
  protects against someone who looks at your screen. It does not protect the
  file.

## Run the tests

You need this only if you changed the code and want to check that it still
works.

```bash
uv sync --dev
uv run pytest
```

Install Node.js too. Some tests check Cozy's JavaScript, and they need the
`node` command.

Without Node.js those tests are **skipped, not failed**. A run that reports
success on a computer without Node.js has tested none of that JavaScript. Read
the last line of the output: it must not say "skipped".

---

**Next:** [Troubleshooting](7-troubleshooting.md) lists common problems and
how to fix each one.

Back to the [list of all guides](../README.md#documentation).
