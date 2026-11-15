# Getting started

This guide takes you from a fresh install to your first chat. There are four
steps:

1. [Connect an AI server](#1-connect-an-ai-server). Cozy cannot reply without
   one.
2. [Check the samplers](#2-check-the-samplers). Usually there is nothing to
   change.
3. [Add a character](#3-add-a-character). One is already included.
4. [Set up your persona](#4-set-up-your-persona). This is who you are in the
   story.

The guide assumes Cozy is installed and open in your browser. If it is not,
follow **Quick start** in the [README](../README.md#quick-start) first.

The first time Cozy opens, it shows a setup page instead of a chat. The setup
covers steps 1, 3 and 4 in a shorter form. Its steps go in order: a step shows
its fields and buttons only once the one before it is done.

- **Connect an AI server.** Pick your service, or **Other**, and Cozy fills in
  the **Endpoint**. Add your **API key** if the server needs one, set the
  **Model**, and click **Connect**. Cozy tests the connection. What you type is
  saved in **Settings → API**, the same as typing it there.
- **Tell it your name.** Type a name and click **Save**. This renames the
  persona **User**.
- **Pick a character.** Click **Chat with** a character to finish the setup and
  open that chat. **Import a card** adds a character from a file first.

The **Theme** menu at the top left changes the theme while you set up.
**Skip**, at the top right, closes the setup without doing the rest. The setup
shows only once: after you finish or skip it, it does not come back. The steps
below do the same things from Settings, and cover what the setup leaves out.

Do the steps in order. Each one says when you are done with it.

## 1. Connect an AI server

Cozy is only the chat app. It does not include an AI, so you must connect it
to a separate AI server. That can be:

- a paid service on the internet, such as OpenRouter or Anthropic, or
- a program on your own computer, such as Ollama, LM Studio, llama.cpp or
  KoboldCpp.

Do this:

1. Click **Settings** at the bottom left of the screen.
2. Click **API** in the list on the left.
3. Under **Connection**, type your server's address into the **Endpoint**
   field. It normally ends in `/v1`. Common ones:

   | Service | Endpoint |
   |---|---|
   | OpenRouter | `https://openrouter.ai/api/v1` |
   | Anthropic (Claude) | `https://api.anthropic.com/v1` |
   | LM Studio | `http://localhost:1234/v1` |
   | Ollama | `http://localhost:11434/v1` |
   | llama.cpp | `http://localhost:8080/v1` |
   | KoboldCpp | `http://localhost:5001/v1` |

   These are the defaults. If you changed a port or address when you set up
   the server, change the address to match. If your service is not in the
   table, look in its documentation for its "OpenAI-compatible base URL".

   Type only the base address. Cozy adds `/models` and `/chat/completions` to
   it by itself.
4. If your server requires an API key, paste it into the **API key** field.
   Servers on your own computer usually need no key.
5. Set the **Model**. Click **Browse** to list the models the server reports,
   and click one. If the list is empty, type the model's name by hand. Some
   servers do not provide a list, and that is normal.
6. Click **Test connection** at the top right of the page.
   - If the test succeeds, go on to step 7.
   - If it fails, see
     [Connection test fails](7-troubleshooting.md#connection-test-fails).
7. Under **Context & generation**, set two numbers:
   - **Max context tokens**: how much text your model can read at once. Your
     server's or model's documentation gives this number.
   - **Max response tokens**: the longest single reply you want.

   If the server reports the model's size and your number is too close to it,
   a warning under the field offers a number that fits. Click the warning to
   use that number.

There is no Save button on this page. Each change is saved as you make it.

You are done with this step when **Test connection** succeeds.

### Keep more than one connection (optional)

A preset is a saved copy of everything on the API page. Use presets if you
switch between servers.

- **Save as preset** saves what is on the page now under a name you type.
- Once you have a preset, a picker appears next to that button. Pick a preset
  to switch to it.
- **Rename** and **Delete** act on the selected preset.

### If you use Anthropic's Claude API

There is no setting to switch on. Cozy recognizes an address on
`anthropic.com`, and any address that ends in `/messages`, and talks to it in
Claude's own format. Three things are different there:

- **Samplers are not sent.** Current Claude models choose their own sampling.
  The **Samplers** card is faded while a Claude address is set, and says so.
- **Prompt caching is on.** The system prompt and chat history that every
  reply repeats cost less after the first message.
- **Max response tokens still applies.** Current Claude models think before
  they answer, and that thinking uses part of this number.

### Where your API key is kept

The key is stored in Cozy's database in your data folder, as plain text. Cozy
never shows the full key again after you type it. See
[What is stored as plain text](6-running-cozy.md#what-is-stored-as-plain-text).

## 2. Check the samplers

Samplers are number settings that control how the AI chooses its words.

Cozy starts with **Temperature 1.0** and **Min-P 0.05**, and every other
sampler off. This suits most models. If nobody has told you to use other
values, you are done with this step. Change nothing.

If your model's documentation recommends specific values:

1. Go to **Settings → API**, to the **Samplers** card.
2. Click the gear icon at the top right of that card. A list called **Active
   samplers** opens.
3. Turn on each sampler that the documentation names.
4. Type in the recommended values.

Only the samplers you turned on are sent to the server.

[Sampler settings](5-sampler-settings.md) explains what each one does and
which servers accept it.

## 3. Add a character

A character is who you talk to. A new install includes one character, Sasha,
so you can start right away. Click her name in the sidebar to open a chat.

You are done with this step when the sidebar has a character you want to talk
to. The rest of this step is for adding more.

### Import a card from another site

Cozy reads V2 character cards, the format SillyTavern uses. A card is either a
`.png` image with the character's data stored inside it, or a `.json` text
file. Sites such as chub.ai give you files in this format.

The quick way, on a computer: drag the card file onto the Cozy window and drop
it anywhere. You can drop several cards at once. Each one is added as a new
character.

The menu way, which also works on a phone:

1. Click **New character** at the top of the sidebar. The character editor
   opens.
2. Click **Import/Export** at the top of the editor.
3. Choose **From file (.json / .png)** and select the card file.
4. Look over the fields, then click **Save**.

Imported cards are stored as PNG files in `data/characters/`, so other apps
can still read them. The same **Import/Export** menu saves the character you
are editing to a `.json` or `.png` file.

### Replace a card with a newer version

When the author of a character you already have publishes an update, put the
new card on top of the old one. This keeps your chats.

1. Point at the character in the sidebar, click **⋯**, then **Edit**.
2. Click **Import/Export**, choose **From file (.json / .png)**, and select
   the new file. Or drop the file onto the window while the editor is open.
3. Cozy asks you to confirm. Confirm.

Your chats with that character, and its place in the sidebar, do not change.

Two warnings:

- **Every field is replaced.** Any edits you made to the old card are lost,
  and there is no undo.
- **A `.json` card has no picture.** Importing one changes only the text and
  keeps the current picture. Import a `.png` file to change the picture too.

### Make a character yourself

1. Click **New character** at the top of the sidebar. The character editor
   opens.
2. Type a **Name**. This is the only required field.
3. Fill in any other fields you want. They are on four tabs:

   | Tab | Field | What it is |
   |---|---|---|
   | **Basic** | **Description** | What the character looks like, their background, who they are. This field matters the most. |
   | | **Personality** | A short list of traits. |
   | | **Scenario** | The situation the story starts in. |
   | **Messages** | **First message (greeting)** | What the character says when a new chat starts. |
   | | **Alternate greetings** | Other opening messages. You can switch between them in a chat. |
   | | **Example messages** | Sample exchanges that show how the character talks. |
   | **Advanced** | **System prompt**, **Post history instructions** | The card author's own instructions. Both are marked **⊘** because Cozy does not send them unless you turn that on. See [Use character prompts](4-prompts-and-memory.md#use-character-prompts). |
   | | **Creator notes** | Notes from the card's author. |
   | **Metadata** | **Tags**, **Creator**, **Version** | Saved with the card when you export it. |

4. To add a picture, click the image at the top left of the editor. Without a
   picture, the character shows its initials.
5. Click **Save**.

In any field, `{{char}}` is replaced by the character's name and `{{user}}`
by your persona's name. See
[Placeholders](4-prompts-and-memory.md#placeholders).

If you close the editor with unsaved changes, Cozy asks before it discards
them.

### Edit, delete, pin or archive a character

Point at a character in the sidebar and click **⋯** next to its name.

| Choice | What it does |
|---|---|
| **Pin** | Keeps the character at the top of the list. A star marks it. |
| **Edit** | Opens the character editor. |
| **Archive** | Moves the character, with its chats, into the folded **Archived** section at the bottom of the list. Click **Archived** to unfold it. **Unarchive** in the same menu brings the character back. |
| **Delete** | Deletes the character and its chats. This cannot be undone. A deleted character does not come back, including the one that came with Cozy. |

When you have more than twelve characters, a clock button appears next to
**New character**. It lists the five characters you wrote to most recently.

## 4. Set up your persona

A persona is who you are in the story. Its name replaces `{{user}}`, and its
description is sent to the AI with every message.

A fresh install has one persona, named **User**. If you leave it, the AI
thinks your name is "User".

1. Click **User** at the bottom of the sidebar. A list of your personas opens.
2. Point at **User** in the list and click the pencil next to it.
3. Type your name in **Name**.
4. In **Description**, describe yourself, or the character you play. This is
   the part the AI reads.
5. Click **Save**.

You are done with this step when the bottom of the sidebar shows your name
instead of **User**.

To keep more than one persona, click **New Persona** in the same list. To
switch, click your name at the bottom of the sidebar and pick another one.

## Start chatting

1. Click a character in the sidebar.
2. Type a message in the box at the bottom.
3. Press **Enter**. On a phone, tap the **Send** button.

The reply appears a few words at a time.

---

**Next:** [Using Cozy](2-using-cozy.md) explains what you can do in a chat.

Back to the [list of all guides](../README.md#documentation).
