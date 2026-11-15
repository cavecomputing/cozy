# Troubleshooting

Find your problem in this list and go to its section. Each section starts
with the thing to try first.

- [Cozy does not open](#cozy-does-not-open)
- [Port 5000 is already in use](#port-5000-is-already-in-use)
- [No models appear](#no-models-appear)
- [Connection test fails](#connection-test-fails)
- [A reply stops early](#a-reply-stops-early)
- [A sampler causes errors](#a-sampler-causes-errors)
- [A summary run keeps failing](#a-summary-run-keeps-failing)
- [A theme is missing or looks wrong](#a-theme-is-missing-or-looks-wrong)
- [You cannot sign in](#you-cannot-sign-in)
- [You cannot change files in `data/`](#you-cannot-change-files-in-data)
- [An update fails](#an-update-fails)
- [The database is too old](#the-database-is-too-old)
- [See more in the logs](#see-more-in-the-logs)

## Cozy does not open

First check that Cozy is running.

- **Python:** look at the terminal where you ran `uv run app.py`. If the
  command has ended, or the terminal is closed, Cozy is not running. Start it
  again. If it shows an error, read the last lines.
- **Docker:** run these two commands. The first lists the container if it is
  running. The second shows its log.

  ```bash
  docker compose -f docker/compose.yml ps
  docker compose -f docker/compose.yml logs -f
  ```

Then check the address. The default is <http://localhost:5000>. If you
started Cozy with a different port, use that port.

## Port 5000 is already in use

Another program is using port 5000. Either stop that program, or give Cozy a
different port:

- **Python:** start Cozy with `uv run app.py --port 8080`. See
  [Use a different address or port](6-running-cozy.md#use-a-different-address-or-port).
- **Docker:** change the port in `docker/compose.yml`. See
  [Use a different port](6-running-cozy.md#use-a-different-port).

## No models appear

**Browse** in **Settings → API** lists the models your AI server reports. If
the list is empty, check these in order:

1. The **Endpoint** is the base address. It normally ends in `/v1`.
2. The AI server is running.
3. The **API key** is correct.

If all three are right, your server does not provide a list of models. Some
do not, and that is normal. Type the model's name into the **Model** field by
hand.

## Connection test fails

Read the error. It has three parts: the status number the server answered
with, what that number usually means, and the server's own reason after "It
said:".

| Status | Usually means | What to do |
|---|---|---|
| 401 | The API key is wrong or missing. | Paste the key again. |
| 402 | Your account with the service has no credits left. | Add credits at the service. |
| 403 | The key is not allowed to use this model, or the server refused the request. | Choose another model, or check the key's permissions at the service. |
| 404 | The endpoint does not end in `/v1`, or the model name is wrong. | Fix the **Endpoint** or the **Model**. |
| 429 | Too many requests. | Wait a moment, then try again. |

**If Cozy says it could not reach the server**, nothing answered at that
address. Check that the AI server is running, and that the address and port
in **Endpoint** are right.

**If Cozy runs in Docker and the AI server runs on the same computer**,
`localhost` does not work as the address. Inside the container, `localhost`
means the container itself. Use an address that Docker can reach. On Docker
Desktop that is `host.docker.internal`, for example
`http://host.docker.internal:11434/v1`.

The same errors can appear in a chat when a reply fails. There, the error has
a **Retry** button. **Retry** asks again for the reply to the message you
already sent, so you do not need to send it twice.

## A reply stops early

Cozy says which of two causes it was.

- **"The reply was cut off at Max response tokens"**: the reply reached the
  limit you set. Raise **Max response tokens** in **Settings → API → Context
  & generation**. A model that thinks before it answers uses part of the same
  limit for its thinking.
- **"The connection closed before the reply finished"**: the server stopped
  sending without saying that it was done. Look at Cozy's log and at the AI
  server's log. If a proxy sits between Cozy and the AI server, it may be
  closing the connection, or holding the reply back instead of passing it on
  as it arrives.

In both cases Cozy keeps the part of the reply that arrived. To get a new
version, click the right arrow (**→**) on the reply. The new version is added
as another [swipe](2-using-cozy.md#get-a-different-reply-swipes).

## A sampler causes errors

Not every AI server accepts every sampler. If messages started to fail after
you turned a sampler on:

1. Turn that sampler off, in **Settings → API → Samplers**.
2. Send the message again.
3. Look in your AI server's documentation for the settings it accepts.

See
[Which servers accept which sampler](5-sampler-settings.md#which-servers-accept-which-sampler).

## A summary run keeps failing

Cozy already tries every batch twice before a run stops. So an error that
comes back each time is caused by a setting.

If the memory panel shows **"cut off by its completion token limit"**, the
summarizer's answer ran out of room. The usual cause is a model that thinks
before it answers: its thinking uses the same token limit as the summary.
Do one of these:

- Raise **Max response tokens** in **Settings → API → Context & generation**.
  The summarizer uses that limit too.
- Or choose a model that does not think, in **Settings → Summaries →
  Summarizer model**. A small, fast model is enough.

The batches that were already finished are kept. The next run continues after
them. See
[If a summary update fails](4-prompts-and-memory.md#if-a-summary-update-fails).

## A theme is missing or looks wrong

See
[Fix a theme that does not work](3-user-themes.md#fix-a-theme-that-does-not-work).

## You cannot sign in

Set a new password and restart Cozy. There is no other way to reset it. The
sign-in page exists only when `COZY_PASSWORD` is set. See
[Require a password](6-running-cozy.md#require-a-password).

## You cannot change files in `data/`

This happens with Docker on Linux. The container makes user 1000 the owner of
everything in `data/`. If your own account has a different ID, build the app
with your IDs:

```bash
docker compose -f docker/compose.yml down
docker compose -f docker/compose.yml build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
docker compose -f docker/compose.yml up -d
```

## An update fails

1. Stop Cozy.
2. Copy the `data/` folder to a safe place, if you have not already.
3. Run the update commands again. They are in
   [Update Cozy](6-running-cozy.md#update-cozy).
4. If it fails again, read the first error in the log.

Do not delete `data/` to fix an update. That folder holds your chats and
settings.

To return to the version you had before, see
[Go back after an update](6-running-cozy.md#go-back-after-an-update).

## The database is too old

Cozy opens a database at version 21 or later. On an older one it stops at
start with an error that begins with the path of the database and says
`is at database version`, followed by a number below 21. Cozy changes nothing
in the database when this happens.

The last Cozy that can update such a database is the Git commit `fc337a2`. To
update the database:

1. Copy the `data/` folder to a safe place.
2. Run `git checkout fc337a2`, then start Cozy once. It updates the database
   to version 21.
3. Stop Cozy.
4. Run `git checkout main`, then follow [Update Cozy](6-running-cozy.md#update-cozy).

A backup file made before version 21 is refused in the same way when you
restore it. Restore it in the Cozy from commit `fc337a2` instead, then update
as above.

## See more in the logs

Cozy writes a log: in the terminal for Python, and in
`docker compose -f docker/compose.yml logs -f` for Docker. The
`COZY_LOG_LEVEL` environment variable sets how much it writes.

| Value | What is logged |
|---|---|
| `WARNING` | Only problems. |
| `INFO` | The default. It includes one line for each chat request and each reply. |
| `DEBUG` | Also the first words of every prompt and reply. |

`DEBUG` puts parts of your chats in the log. Set the level back when you have
found the problem.

Python:

```bash
COZY_LOG_LEVEL=DEBUG uv run app.py
```

Docker: put the line `COZY_LOG_LEVEL=DEBUG` in the file `docker/.env`, then
run `docker compose -f docker/compose.yml up -d`.

---

This is the last guide. Back to the
[list of all guides](../README.md#documentation).
