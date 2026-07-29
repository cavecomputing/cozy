# Getting Started

This page takes Cozy from a fresh download to a working chat.

## 1. Start Cozy

Choose one method. Run the commands from the repository root.

### Docker

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### Python

```bash
uv sync
uv run python app.py
```

Open <http://localhost:5001>.

## 2. Connect an LLM server

Cozy is a chat interface. It does not include an LLM or download models.

1. Open **Settings**.
2. In **Connection**, enter the server's base URL. It normally ends in `/v1`.
3. Enter an API key if the server requires one.
4. Select a model.
5. Use the connection test.

Example base URL:

```text
http://localhost:8080/v1
```

Cozy appends `/models` and `/chat/completions` to this URL. The chat endpoint
must support streaming responses.

## 3. Add a character

Use either method:

- Import a SillyTavern-compatible V2 `.png` or `.json` character card.
- Create a character in Cozy.

Select the character and start a chat.

## 4. Optional setup

- Open the persona menu at the bottom of the sidebar to create a persona.
- Choose a system prompt under **Settings → Prompt**.
- Enable Auto Summaries for long chats from the memory button beside the chat
  input.
- Select a regex output filter preset under **Settings → Regex** if replies need
  cleaning up, such as straightening non-English quotation marks. No filtering
  happens until you select one.

Cozy saves changes automatically unless the screen shows a **Save** button.
