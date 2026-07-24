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

Stop the program using port 5001 or change Cozy's host port. See
[Use a different port](run.md#use-a-different-port).

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

## An update fails

1. Stop Cozy.
2. Back up `data/`.
3. Run the update commands again.
4. Read the first error in the logs.

Do not fix an update by deleting `data/`; that directory contains your chats and
settings.
