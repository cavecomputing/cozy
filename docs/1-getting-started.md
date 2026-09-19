# Getting Started

This page assumes Cozy is already installed and open in your browser. If it
is not installed yet, follow **Quick start** in the
[README](../README.md) first.

Work through the sections in order. Each section says when you are done
with it.

## 1. Connect an AI server

Cozy is only the chat app. It does not include an AI, and it cannot think
on its own. You must connect it to a separate AI server. That can be a paid
service on the internet (for example OpenRouter) or a program running on
your own computer (for example Ollama, LM Studio, llama.cpp, or KoboldCpp).

1. Click **Settings** at the bottom left of the screen. A settings panel
   opens.
2. Click **API** at the top of the settings panel.
3. Click **+** to create a connection preset, then type a name for it and
   confirm. A preset stores all the connection settings described below,
   so you can keep one preset per server and switch between them. A new
   preset starts as a copy of whatever is currently on the page.
   **Rename** renames the selected preset.
4. Under **Connection**, type your server's base **Endpoint** URL into the
   **Endpoint** field. It normally ends in `/v1`. Some common ones:

   | Service | Endpoint |
   |---|---|
   | OpenRouter | `https://openrouter.ai/api/v1` |
   | LM Studio | `http://localhost:1234/v1` |
   | Ollama | `http://localhost:11434/v1` |
   | llama.cpp | `http://localhost:8080/v1` |
   | KoboldCpp | `http://localhost:5001/v1` |

   These are the defaults. If you changed a port or address when setting
   up the server, change the URL to match. If you do not know the endpoint
   for your service, look in that service's documentation for its
   OpenAI-compatible base URL. Cozy adds `/models` and `/chat/completions`
   to this URL itself, so do not type those parts.
5. If your server requires an **API key**, paste it into the **API key**
   field. Servers on your own computer usually need no key. Keys are stored
   in your data directory and hidden in API responses.
6. Set the **Model**. Type the model name, or click the chevron button to
   list what the server reports and pick one from the list. If the list
   comes up empty, type the name by hand. Some servers do not provide a
   list, and that is normal.
7. Click **Test Connection**. If the test succeeds, you are done with this
   section. If it fails, see [Troubleshooting](4-troubleshooting.md).
8. Under **Context & generation**, set **Max context tokens** to the
   context size your server supports, and **Max response tokens** to the
   longest single reply you want to receive.

## 2. Check the samplers

Samplers are number settings that control how the model chooses its words.
Three common ones are Temperature, Min-P, and Repetition penalty. The
default values work for most models. Change them only if the documentation
for your model recommends specific values.

Only the samplers you turn on are sent to the server. The rest are
ignored.

1. Go to **Settings → API → Samplers**.
2. Click the gear icon in the **Core samplers** header. A list called
   **Active samplers** opens.
3. Turn on each sampler your model or server documentation names.
4. Type in the recommended values.

A starting point that works for general chat:

```text
Temperature: 0.8
Min-P: 0.05
Repetition penalty: 1.05
```

What every setting means, and which servers support it:
[Sampler settings](6-sampler-settings.md).

## 3. Add a character

A character is who you talk to. A new install includes one character, so
you can start right away. You can keep her, change her, or delete her. A
deleted character does not come back.

### Bring in a card from another site

Cozy reads V2 character cards, the same format SillyTavern uses. A card is
either a `.png` image with the character data stored inside it, or a plain
`.json` text file. Sites such as chub.ai hand out exactly this format.

1. Click **+** at the top right of the character sidebar (the left
   column). The character editor opens.
2. Click **Import/Export** in the editor header.
3. Under **Import**, choose **From file (.json / .png)** and select the
   card file.
4. Look over the fields, then save.

Imported cards are stored as PNG files in `data/characters/`, so other
apps can still read them. The same **Import/Export** menu saves the
character you are editing back out as `.json` or `.png`.

### Replace a card with a newer version

When a character you already have gets an update, place the new card on
top of the existing character instead of adding a second copy:

1. Open the character for editing.
2. Use the same **Import** menu item and select the new file.
3. Cozy asks you to confirm. Confirm, and the new card replaces the old
   one in place.

Your chats with that character, and their place in the sidebar, do not
change. Two warnings. First, the replacement replaces every field. Your
own edits to the old card are lost, and there is no undo. Second, a
`.json` card contains no picture, so importing one keeps the current
picture and changes only the text. Import a `.png` file to change the
picture too.

### Make a character yourself

1. Click **+** at the top right of the character sidebar. The character
   editor opens.
2. Fill in the fields. Only a name and an avatar picture are required.
   Everything else is optional.

What the fields mean:

- **Description** — what the character looks like, their background, who
  they are. This field matters the most.
- **Personality** — a short list of traits.
- **Scenario** — the situation the story starts in.
- **First message (greeting)** — the line the character says when a new
  chat starts.
- **Alternate greetings** — extra opening lines you can switch between.
- **Example messages** — sample exchanges that show how the character
  talks and formats replies.
- **System prompt** and **Post history instructions** — extra instructions
  that apply to this character only.
- **Creator notes**, **Tags**, **Creator**, **Version** — saved with the
  card when you export it.

In any of these fields, `{{char}}` becomes the character's name and
`{{user}}` becomes your persona's name (see section 4 below).

A field marked with ⊘ is skipped because your prompt template does not
include it. Hover the marker to see which part is missing. The same marker
appears next to Author's Note, Active Lorebook, or Auto Summary in the
memory button's panel when the template leaves that part out.

If you close the editor with unsaved changes, Cozy asks whether to discard
them before it closes. This happens no matter how you close it: the
**Cancel** button, the **✕**, the Escape key, a click outside the editor,
or opening another panel.

Click the character in the sidebar to start chatting with them.

## 4. Set up your persona

A persona is who *you* are in the story. Its name is used wherever
`{{user}}` appears, and its description is sent to the AI with every
message you send.

1. Click your name at the bottom of the sidebar. Your personas open.
2. Create a persona and write a short description of yourself, or of the
   character you play.
3. Save. To switch to a different persona later, click your name again
   and pick another one.

You are done with this section when you have one persona saved.

## 5. Change the look and behavior (optional)

Nothing in this section is needed to chat. Skip it and come back later if
you want.

- **Prompt template** — **Settings → Prompt** holds the template that
  combines the character, your persona, lorebook entries, and chat history
  into the text the AI receives. Cozy includes several ready-made presets.
  The eye icon on **Settings → API** shows exactly what will be sent. The
  pill beside a preset's name is its version, and two versions of the same
  preset can sit side by side once an update ships.
  **Import/export** is always available, so a preset someone shares with you
  can be loaded straight in. The buttons that author a preset — **+**, which
  copies the selected one so you can edit the copy, **Rename** and
  **Delete** — appear once **Show advanced configuration** is ticked in
  **Settings → General**. Do not rename a bundled preset: Cozy restores the
  original under its old name on the next start, because the files in
  `default_prompts/` are the source of truth for those.
- **Theme** — **Settings → General → Appearance** changes the colors. This
  choice is stored in the browser, so each browser can have its own theme.
  You can also add your own theme file to `data/themes/`; see
  [User themes](9-user-themes.md).
- **Slash commands** — typing `/` in the chat box lists the available
  commands. `/prompt <name>` and `/api <name>` switch prompt and API
  presets without opening Settings. Keep typing after the space and
  matching presets are offered. Typed with no name, they show what is
  active and what is available. Where two versions of the same prompt are
  installed, `/prompt <name>` picks the newer one and
  `/prompt <name> <version>` — `/prompt NanoBear 2.1` — picks the exact one.

On a phone, Settings opens as a list. Tap a section to open its page, and
tap the back arrow to return to the list. In the Prompt editor,
**Variables** opens a list of everything the template can use.

Cozy saves most changes immediately. If a page has a **Save** button,
click it, or your change is lost.

## 6. If something goes wrong

- The connection test fails, or replies never arrive →
  [Troubleshooting](4-troubleshooting.md).
- Old messages fall out of long chats and the character forgets them →
  [Auto Summaries](5-auto-summaries.md). Turn it on for one chat at a time
  from the memory button next to the chat input.
- Replies keep the same repeated formatting problem, such as wrong
  quotation marks → [Regex output filters](8-regex-output-filters.md). Nothing is filtered
  until you select a preset under **Settings → Regex**.
- You want background facts the AI uses only when relevant, such as
  places, history, or side characters → manage them under
  **Settings → Lorebooks**, and attach them from the memory button next
  to the chat input.
- Before updating Cozy, copy the `data/` directory somewhere safe.
  Everything you made lives there, and going back to an older version is
  not supported. → [Data and backups](3-data-and-backups.md).
