# Prompts and memory

This guide explains what the AI is told with each message, and how you change
it.

Each time you send a message, Cozy sends the AI these parts, in this order:

1. Your **System** text, from **Settings → Prompt**.
2. The character: its description and personality, then its example messages.
3. Your persona's description.
4. The lorebook entries this chat brought up, then the character's scenario.
5. The chat's Author's Note, then its Auto Summary.
6. The chat messages, oldest first.
7. Your **Post-History** text, from **Settings → Prompt**.

Cozy wraps each part in a labeled tag, so the AI can tell the parts apart. A
part with nothing in it is left out. You cannot change the order.

You do not have to change anything here to chat. Cozy's defaults work.

Go to the part you need:

- [The prompt](#the-prompt): the two texts you write.
- [Placeholders](#placeholders): codes such as `{{char}}` that Cozy fills in.
- [Author's Note](#authors-note): text the AI gets with every message in one
  chat.
- [Lorebooks](#lorebooks): facts the AI gets only when the chat mentions them.
- [The context meter](#the-context-meter): how much the AI can read, and what
  happens when a chat is longer than that.
- [Auto Summaries](#auto-summaries): a summary of the old messages the AI can
  no longer read.

## The prompt

The prompt is the set of instructions the AI gets with every message. You
write two pieces of text. Cozy adds the other parts in the list above.

Open **Settings → Prompt**. The **Instructions** section holds two texts, and
the **System** / **Post-History** switch beside its title picks which one you see:

| Text | When it is sent | What to write in it |
|---|---|---|
| **System** | First, before everything else. | How the AI should tell the story. |
| **Post-History** | Last, after the chat, just before each reply. | A short reminder. It is the last thing the AI reads before it writes. |

Cozy comes with two ready-made presets, **NanoBear** and **NanoBear Author**.
A fresh install uses **NanoBear**.

### See what will be sent

- The **?** button at the top of the Prompt page shows the whole layout.
- **Preview**, on the editor, shows both of your texts in their tags.
- The eye button at the top of **Settings → API** shows exactly what will be
  sent for the open chat.

### Presets

A preset is one saved pair of System and Post-History text. The picker at the
top of the Prompt page switches between them.

| Button | What it does |
|---|---|
| **New** | Makes a copy of the selected preset, for you to edit. |
| **Rename** | Renames the selected preset. |
| **Delete** | Deletes the selected preset. |
| **Import/export** | Loads a preset from a file, or saves the selected one to a file. |

The pill next to a preset's name, such as **v3.0**, is its version. An update
to Cozy can add a newer version of a bundled preset. The older version stays,
so both are in the list.

Do not rename or delete **NanoBear** or **NanoBear Author**. Cozy puts the
bundled presets back each time it starts. To make your own version, click
**New** and edit the copy.

### Use character prompts

Some character cards include a system prompt or post-history instructions of
their own. By default Cozy does not send them. It sends yours.

To send the card's text instead, turn on **Use character prompts**, under the
editor. Then, while you chat with a character whose card has its own text:

- The card's system prompt replaces your **System** text.
- The card's post-history instructions replace your **Post-History** text.
- The editor shows the card's text, so you can see what is being sent. You cannot
  edit it there.

Your own text is not changed, and it is still used for every other character.

## Placeholders

A placeholder is a short code that Cozy replaces before the text is sent.

| You write | Cozy sends |
|---|---|
| `{{char}}` | The character's name. |
| `{{user}}` | Your persona's name. |
| `{{time}}` | The current time on your device. |
| `{{date}}` | The current date on your device. |
| `{{random:a,b,c}}` | One of the options, picked again each time. |

They work in the two prompt boxes, in character cards, in personas and in
lorebook entries.

Any other `{{...}}` code in a prompt box is removed and nothing is sent for it.

## Author's Note

The Author's Note is text that is sent to the AI with every message in one
chat. Use it for facts the AI must never forget: a rule, a goal, where the
scene is.

1. Open the chat.
2. Click the memory button, the brain at the left of the chat box. The memory
   panel opens.
3. Type in the **Author's Note** box.

The note is saved as you type. It belongs to the open chat only. A new chat
starts with an empty note.

The memory panel has two more parts, and both also belong to the open chat
only:

- **Active Lorebook** chooses the lorebook this chat uses. See
  [Lorebooks](#lorebooks).
- **Auto Summary** turns the summary on or off. See
  [Auto Summaries](#auto-summaries).

## Lorebooks

A lorebook is a list of facts about your story's world. The AI is sent a fact
only when the recent messages mention one of that fact's keywords. This keeps
background details out of the way until they matter.

Each chat uses one lorebook at a time, or none.

### Make a lorebook

1. Open **Settings → Lorebooks**.
2. Click **New**. To load a SillyTavern lorebook file instead, click
   **Import** and choose a `.json` file.
3. Type a **Name**.
4. Click **Add entry**. A new entry appears at the top of the list.
5. In the entry's first box, the one that reads `key1, key2`, type the
   keywords with commas between them. For example: `tavern, barkeep, ale`.
6. In the large box under it, type the fact. This is the only part of an
   entry that is sent to the AI.
7. Click **Save**, next to the name.

Nothing on this page is saved until you click **Save**. If you leave with
unsaved changes, Cozy asks first.

To change a lorebook later, pick it from the list at the top of the page.

### Use a lorebook in a chat

1. Open the chat.
2. Click the memory button.
3. Under **Active Lorebook**, pick the book.

The choices are **None**, the character's own lorebook if its card has one,
and each lorebook you made. A new chat starts on the character's own lorebook
if it has one.

### How an entry is chosen

- Cozy checks the last 20 messages for keywords. **Scan depth** changes that
  number.
- A keyword matches a whole word only. The keyword `tavern` matches "the old
  tavern". It does not match "taverns".
- Capital letters do not matter, unless the entry's **Case-sensitive** box is
  checked.
- At most 20 entries are sent with one message. **Max entries** changes that
  number.

### Entry settings

| Setting | What it does |
|---|---|
| **Enabled** | Unchecked: the entry is skipped. It is not deleted. |
| **Always include** | Checked: the entry is sent with every message, and its keywords are ignored. |
| **Case-sensitive** | Checked: only the exact capital letters match. |
| **Require secondary keys** | Checked: the entry is used only when one keyword and one secondary keyword both appear. |
| **Secondary keys** | A second list of words. It does nothing unless **Require secondary keys** is checked. |
| **Insertion order** | A number, 100 by default. When several entries are sent, the lowest number goes first. |
| **Comment** | A note to yourself. It is never sent to the AI. |

### Where a lorebook is saved

**Saved in** on the Lorebooks page decides this. Changing it and clicking
**Save** moves the book.

- **Standalone:** saved in Cozy's database. Any chat can use it.
- **Embedded:** saved inside one character's card file. It travels with the
  card when you export or share it.

The **?** button at the top of the Lorebooks page shows this help inside Cozy.

## The context meter

The AI can only read a limited amount of text at once. That limit is the
context, and you set it as **Max context tokens** under **Settings → API**.
The context meter shows how much of it the next message will use.

- The numbers at the right, such as **5,647 / 65,536**, are tokens used and
  the limit. A token is about three quarters of a word.
- Each colored segment is one part of what is sent: your prompt, the
  character, your persona, lorebook entries, the Author's Note, the summary,
  the chat messages, what you are typing, and the room kept for the reply.
- Point at a segment, or tap it, to see its name and size.
- The label turns to a warning color at 70% full, and to the danger color at
  90%.

**When a chat no longer fits:** Cozy leaves out the oldest messages. Nothing
is deleted. They are still in the chat, but the AI does not see them. A line
labeled **Context window** appears in the chat above the oldest message the AI
still sees. To jump to that line, click the **Message history** segment of the
meter, or type `/jump`.

To make the AI remember what was left out, turn on
[Auto Summaries](#auto-summaries).

To hide or show the meter, type `/meter`, or use **Settings → General → Show
context token meter**.

## Auto Summaries

An Auto Summary is a short summary of the old messages that no longer fit in
the context. Cozy writes it by asking an AI model, and sends it with every
message, so the AI still knows what happened earlier in the story.

Auto Summary is off for a new chat. You turn it on for each chat that should
have one.

### Turn it on

1. Open the chat.
2. Click the memory button, the brain at the left of the chat box.
3. Turn on the **Auto Summary** switch.

Typing `/auto-summary on` in the chat box does the same. `/auto-summary off`
turns it off.

What happens next depends on the chat:

- **The whole chat still fits in the context.** Nothing is summarized yet. The
  panel says "No aged-out history yet". Cozy starts by itself when the oldest
  message no longer fits.
- **Some messages already do not fit.** Cozy summarizes all of them now. The
  panel shows the progress, such as "Summarizing… (batch 1/12)".

Summarizing sends your old chat messages to an AI server. With a paid
service, this costs money. With a model on your own computer, it takes time.

### What you see in the memory panel

While Auto Summary is on, the **Auto Summary** part of the memory panel shows:

- **A status line.** For example: "Up to date · 24 messages summarized ·
  ≈ 310 / 6,553 tokens". The two numbers are the size of the summary and the
  largest size it may have. The sign ≈ means the numbers are estimates.
- **Story so far.** A list of entries, oldest first. Each entry is a few
  sentences about one stretch of the chat. The label at the end of an entry,
  such as "messages 1–12", says which messages it covers.
- **Bonds.** One entry for each relationship in the story, saying how that
  relationship stands now.

You cannot edit the summary. To write something the AI must always know, use
the [Author's Note](#authors-note).

### Choose the model that writes summaries

By default the summary is written by the same server and model you chat with.
You do not need to set anything.

To use a different model for summaries, for example a cheaper or faster one:

1. Open **Settings → Summaries**.
2. Under **Summarizer model**, fill in **Endpoint**, **API key** and
   **Model**.

You can fill in only some of the three fields. A field you leave empty uses
the value from **Settings → API**, which is shown there in grey. For example,
to use another model on the same server, fill in only **Model**.

A small model is enough. It only has to write a few sentences.

### Stop a run

While a summary is being written, a **Stop** button (a square) appears next to
the **Auto Summary** switch. Click it when a long run takes more time than you
want to wait.

- Cozy summarizes in batches, and saves after each batch. Every batch that was
  already finished is kept.
- The request that was already sent to the summarizer is allowed to finish.
  The run stops after it.
- The messages that were not summarized yet are still in the chat. The status
  line says how many. Cozy continues with them the next time you send a
  message.

Stopping does not change your chat messages.

### Reset the summary

**Reset** (the circular arrows, next to the switch) deletes the summary and
writes it again from the chat's messages. Cozy asks before it does this.

Use it when the summary is out of date. Two things cause that: you edited or
deleted an old message that was already summarized, or you changed **Max
context tokens**.

The old summary is deleted when you confirm, and it cannot be brought back. If
you might want it back, [download a backup](6-running-cozy.md#back-up-and-restore)
first.

Resetting does not change your chat messages.

### If a summary update fails

A failed update never stops you from chatting.

- Each batch is tried twice. The status line says "retrying" during the
  second try. If the second try also fails, the run stops and the panel shows
  the error. The batches that were already finished are kept.
- Your message is still sent and you still get a reply. A warning says how
  many older messages that reply cannot see. Those messages are not lost. They
  are still in the chat, and the next update that succeeds summarizes them.
- After a failure, Cozy tries again in the background after every reply. The
  panel shows the error until a try succeeds.

If the error is "cut off by its completion token limit", see
[A summary run keeps failing](7-troubleshooting.md#a-summary-run-keeps-failing).

### How a summary is built

You do not need this part to use Auto Summaries. It explains the numbers.

**When Cozy summarizes.** Nothing is summarized while the whole chat fits in
the context. When the oldest messages no longer fit, Cozy summarizes them one
batch at a time.

**What a batch is.** A batch is an amount of text, not a number of messages:
about 3,200 tokens of chat. A batch is never more than one eighth of **Max
context tokens**, so with a context of 8,192 a batch is about 1,024 tokens.
Long messages fill a batch sooner than short ones. One message that is longer
than a batch is a batch by itself.

**What a batch becomes.** Each batch becomes exactly one **Story so far**
entry of at most about 240 tokens. A new entry is added at the end. The
entries that are already there are not rewritten. If the summarizer writes an
entry that is too long, Cozy asks it once more. If the second answer is still
too long, Cozy keeps the part that fits and says so in the status line.

**Bonds.** A relationship is kept as one entry of at most about 200 tokens.
When the relationship changes, that entry is rewritten. No second entry is
added.

**The size limit.** The summary may use at most 10% of **Max context
tokens**. When it is full, the oldest **Story so far** entries are deleted to
make room for new ones. An entry is never shortened to make it fit, with one
exception: the last entry left in a section is cut short, and ends in "…". If
the **Bonds** no longer fit, the relationship that was added most recently is
deleted first.

So the summary remembers the recent past, not the whole chat. For anything the
AI must always know, use the [Author's Note](#authors-note). It is sent word
for word with every message and is never deleted.

To change the 10%, set **Size cap** in the **Behavior** card in **Settings →
Summaries**.

**While an update is running.** If you send a message while a summary update
is running, Cozy waits for the update before it asks for the reply. This way
the reply can see the summary of the messages that no longer fit.

**Models that think before they answer.** Each summary request may use as many
tokens as **Max response tokens** in **Settings → API**. A thinking model
spends part of that on thinking before it writes the summary. If **Max
response tokens** is low, the summary is cut off and the run fails. Raise
**Max response tokens**, or choose a model that does not think as the
summarizer model.

### Branches

A [branch](2-using-cozy.md#branch-a-chat) keeps the summary of the chat it was
made from, and Auto Summary stays on in it.

- If you branch at a reply that is newer than everything in the summary, the
  branch gets the whole summary.
- If you branch at a reply that the summary already covers, the branch gets
  only the **Story so far** entries for the messages before that reply. It
  gets no **Bonds**, because a **Bonds** entry may describe things that
  happened after that reply.

### Limits

- How good the summary is depends on the model that writes it.
- The summary holds the recent past only. Old entries are deleted when it is
  full.
- The summary uses part of the context, so a little less of the chat itself
  fits.
- Editing or deleting a message that was already summarized does not change
  the summary. Use **Reset** to write the summary again.

---

**Next:** [Sampler settings](5-sampler-settings.md) explains the numbers that
control how the AI chooses its words.

Back to the [list of all guides](../README.md#documentation).
