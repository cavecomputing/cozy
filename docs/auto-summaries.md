# Auto Summaries

Auto Summaries keep a compressed memory of old chat messages after those
messages no longer fit in the model's context window.

They are disabled on new chats.

## Configure

Open **Settings → Auto Summaries**.

The summarizer can use the main LLM connection. Leave its endpoint, API key, or
model blank to use the matching main connection value.

Settings:

- **Size cap:** Maximum summary size as a percentage of the context window.
- **Messages between updates:** How many additional messages Cozy aims to make
  room for when context pressure triggers a summary update. Cozy estimates this
  token headroom from recent message sizes. The same value limits the number of
  old messages processed by one summarizer request.

The message target is approximate rather than a strict schedule. Cozy does not
summarize while everything fits, and an unusually long message can force an
earlier update. One user or character message counts as one message.

Summarization sends old chat content to the selected LLM server and may add API
cost or local processing time.

## Enable for a chat

1. Open the chat.
2. Open the memory panel beside the chat input.
3. Enable **Auto Summary**.

Existing old messages are processed in batches. Sending a new message may wait
for an active summary update so that no old history is skipped.

## Pins

The summary contains separate generated lines. Pin a line to keep it
word-for-word during later updates.

Pinned lines count toward the summary size cap. Too many pins leave less space
for automatically managed lines.

## Rebuild and reset

- **Rebuild from history** creates the summary again from the stored messages.
- **Reset** clears the current summary and its pins.

Rebuild after editing, deleting, or changing a message that has already been
summarized. Old summary content is not corrected automatically.

## Limits

- Summary quality depends on the selected model.
- Compression may omit details.
- The summary uses part of the model's context budget.
- Editing an already summarized message does not automatically rewrite the
  summary.
