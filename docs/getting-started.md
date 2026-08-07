# Getting Started

This page takes Cozy from a fresh download to a working chat, then points at the
rest of the manual.

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

For ports, network addresses, custom data directories, logs and updating, see
[Running and updating Cozy](run.md).

## 2. Connect an LLM server

Cozy is a chat interface. It does not include an LLM or download models. You
need an OpenAI-compatible server — a hosted one such as OpenRouter, or something
local such as llama.cpp, Ollama or KoboldCpp.

1. Open **Settings** in the bottom left.
2. Go to **API**.
3. Click **+** to create a preset and give it a name. Every connection setting
   below belongs to the selected preset, so you can keep one per server and
   switch between them.
4. Under **Connection**, enter the base **Endpoint** URL. It normally ends in
   `/v1`:

   ```text
   https://openrouter.ai/api/v1
   ```

   ```text
   http://localhost:8080/v1
   ```

5. Enter an **API Key** if the server requires one. Keys are stored in your data
   directory and masked in API responses.
6. Set the **Model**. Type the identifier, or use the chevron button to browse
   and search what the server reports.
7. Click **Test Connection**.

Cozy appends `/models` and `/chat/completions` to the endpoint. The chat
endpoint must support streaming. Model listing varies by server — if the browse
list comes up empty, type the model identifier by hand.

Under **Context & Generation**, set **Max Context Tokens** to match your server's
context window and **Max Response Tokens** to how long a single reply may run.

If the connection test fails or replies never arrive, see
[Troubleshooting](troubleshooting.md).

## 3. Check the samplers

**Settings → API → Samplers** controls how the model picks its words —
temperature, repetition penalty and so on. The defaults work; you only need this
step if the model you chose recommends particular values.

Only the samplers you turn on are sent. Click the gear icon in the **Core
samplers** header to open **Active samplers** and enable the ones your model or
server calls for, then set their values.

A reasonable general-purpose starting point:

```text
Temperature: 0.8
Min-P: 0.05
Repetition penalty: 1.05
```

Every setting, what it does, and which backends support it:
[Sampler settings](samplers.md).

## 4. Add a character

A fresh install ships with one character so you have something to talk to right
away. She is ordinary user data — delete her and she stays gone.

### Import a card

Cozy reads SillyTavern-compatible **V2 character cards**, as either a `.png`
with embedded card data or a plain `.json`. Sites such as chub.ai hand out
exactly this format.

1. Click **+** at the top right of the character sidebar.
2. Click **Import/Export** in the editor header.
3. Under **Import**, choose **From file (.json / .png)** and pick the card.
4. Review the fields, then save.

Cards are stored as PNG files in `data/characters/`, so anything you import
stays in the format other apps can read. The same **Import/Export** menu exports
the character you are editing back out as `.json` or `.png`.

### Update a card to a newer version

When a card you already have gets a new release, import it **on top of** the
existing character instead of adding a second copy. Open the character for
editing first, then use the same **Import** menu item — Cozy asks you to
confirm, then replaces that character's card in place. Your chats with them,
and their place in the sidebar, are untouched.

Two things to know. The replacement is wholesale, not a merge: every field
comes from the new card, so your own edits to the old one are gone and there is
no undo. And a `.json` card has no picture of its own, so importing one keeps
the current image and changes only the text; import a `.png` to change both.

### Create one yourself

Click **+** at the top right of the character sidebar and fill in the editor. A
name and an avatar image are required; everything else is optional.

**Basic**

- **Description** — appearance, background, who they are. This is the field that
  does most of the work.
- **Personality** — a short summary of traits.
- **Scenario** — the situation the story starts in.

**Messages**

- **First Message (Greeting)** — the character's opening line, shown when a new
  chat starts.
- **Alternate Greetings** — extra openers you can swipe between.
- **Example Messages** — sample exchanges that demonstrate voice and formatting.

**Advanced**

- **System Prompt** and **Post History Instructions** — per-character
  instructions that slot into the prompt template.

**Metadata**

- **Creator Notes**, **Tags**, **Creator**, **Version** — travel with the card
  when it is exported.

In any of these fields, `{{char}}` is replaced with the character's name and
`{{user}}` with your active persona's name.

A field marked with ⊘ has content that your active prompt template does not
include, so it will not be sent. That can be deliberate — hover the marker to
see which variable is missing. The same marker appears in the memory button's
flyout beside Author's Note, Active Lorebook or Auto Summary whenever the
template leaves that variable out — whether or not you are using the feature
yet.

Select the character in the sidebar to start chatting.

## 5. Make it yours

- **Persona** — click your name at the bottom of the sidebar to create one. The
  persona is who *you* are in the story, and its description is sent with each
  message.
- **System prompt** — **Settings → Prompt** holds the template that assembles
  character, persona, lorebook and chat context. Several presets ship with Cozy;
  the eye icon previews exactly what will be sent.
- **Theme** — **Settings → General → Appearance** switches between the built-in
  themes. The choice is per-browser. You can also drop your own CSS file into
  `data/themes/`; see [User themes](themes.md).

Cozy saves changes automatically unless the screen shows a **Save** button.

## 6. Advanced

None of this is needed to chat. Reach for it when a specific problem shows up.

- **Auto Summaries** — long chats eventually push their oldest messages out of
  the context window. Auto Summaries condenses what aged out into running notes
  so the character keeps the thread. Turn it on per chat from the memory button
  beside the chat input. → [Auto Summaries](auto-summaries.md)

- **Regex output filters** — a named, ordered list of find/replace rules applied
  to finished replies, for cleaning up recurring output problems such as
  non-English quotation marks or unwanted narration. Cozy ships some presets,
  but **no filtering happens until you select one** under **Settings → Regex**.
  Presets can be imported and exported, and SillyTavern regex scripts are
  accepted. → [Regex output filters](regex.md)

- **Lorebooks** — entries that are injected into the prompt only when their
  keywords appear in recent messages, which is how you give a setting more
  background than fits in a character card. Manage them under **Settings →
  Lorebooks**, and attach them from the memory button beside the chat input.

- **Backups** — everything you have made lives in `data/`. Cozy changes often
  and downgrading is not supported, so copy that directory before updating. →
  [Data and backups](data-and-backups.md)
