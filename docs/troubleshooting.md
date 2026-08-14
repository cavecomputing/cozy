# Troubleshooting

## Cozy does not open

Check whether it is running.

Docker:

```bash
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f
```

Python: read the terminal where `uv run python app.py` is running.

The default address is <http://localhost:5001>.

## Port 5001 is already in use

Stop the program using port 5001 or change Cozy's port. Under Docker, change the
port mapping — see [Use a different port](run.md#use-a-different-port). Under
Python, pass `--port` — see
[Use a different address or port](run.md#use-a-different-address-or-port).

## Docker cannot write to `data/`

On Linux, rebuild the image with your UID and GID:

```bash
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
docker compose -f docker/docker-compose.yml up -d
```

## No models appear

Check these items:

1. The endpoint is the base URL, normally ending in `/v1`.
2. The LLM server is running.
3. The API key is correct.
4. The server provides a `/models` endpoint.

You can enter a model name manually if the server supports chat completions but
does not provide a model list.

## Connection test fails

The error shown by Cozy normally contains the upstream server's response. Check
the endpoint, key, and model name first.

If Cozy runs in Docker but the LLM server runs on the host, `localhost` inside
the container refers to the container itself. Use a host address that Docker can
reach, such as `host.docker.internal` on Docker Desktop.

## A sampler causes request errors

Not every OpenAI-style server accepts every sampler.

1. Disable the sampler.
2. Try the request again.
3. Check the LLM server's documentation.

See [Sampler settings](samplers.md#backend-compatibility).

## Replies stop early

Check **Max Response Tokens** first. If the reply is not reaching that limit,
inspect both the Cozy logs and the LLM server logs. A proxy between Cozy and the
LLM server may be buffering or closing the streaming response.

## Replies arrive with text changed or missing

A regex output filter rewrites each reply before it is saved, so a stray pattern
can delete more than intended. Open **Settings → Regex** and check which preset
is selected; select **None** to switch filtering off. Filters change the stored
message and cannot be undone.

If the rule is meant to dress a reply up rather than correct it — building a
stat card or a progress bar — tick **Display only** on it. The filter then
rewrites the bubble and leaves the stored message alone, which is also the fix
for a model that has started writing HTML into its replies by itself.

See [Regex output filters](regex.md).

## A summary run keeps failing

If the status line shows *"cut off by its completion token limit"*, the
summarizer's reply ran out of room. The usual cause is a reasoning model: its
thinking is billed against the same token allowance as the summary itself.

1. Raise **Max Response Tokens** in **Settings → API → Context & Generation** —
   the summarizer borrows that allowance.
2. Or set a non-reasoning **Auto Summaries** model. The summarizer only writes a
   few bullets, so a small fast model suits it.

Batches already folded in are kept, and the next run resumes from there. See
[Auto Summaries](auto-summaries.md#configure).

## An update fails

1. Stop Cozy.
2. Back up `data/`.
3. Run the update commands again.
4. Read the first error in the logs.

Do not fix an update by deleting `data/`; that directory contains your chats and
settings.
