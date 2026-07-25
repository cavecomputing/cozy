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
- **Batch size:** How many of the oldest messages Cozy folds into the summary in
  one update. The same value limits the number of old messages processed by one
  summarizer request.

Cozy does not summarize while everything still fits. Once the oldest message no
longer fits, it summarizes exactly one batch and drops those messages from the
raw transcript. One user or character message counts as one message, whatever
its length.

A larger batch means fewer, better-compressed updates, but a bigger drop in
verbatim history each time one runs. If a chat holds fewer messages than the
batch size, Cozy retires what it can and always keeps the newest message.

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
