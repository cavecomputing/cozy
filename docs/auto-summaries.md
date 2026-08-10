# Auto Summaries

Auto Summaries keep a condensed memory of old chat messages after those
messages no longer fit in the model's context window.

They are disabled on new chats.

## Configure

Open **Settings → Auto Summaries**.

The summarizer can use the main LLM connection. Leave its endpoint, API key, or
model blank to use the matching main connection value.

Settings:

- **Size cap:** Maximum summary size as a percentage of the context window.
- **Batch size:** How many of the oldest messages Cozy folds into the summary in
  one update. Each batch becomes exactly **one** summary entry.

Cozy does not summarize while everything still fits. Once the oldest message no
longer fits, it summarizes exactly one batch and drops those messages from the
raw transcript. One user or character message counts as one message, whatever
its length.

Batch size is the whole contract: 10 messages in, one entry out. A larger batch
means fewer, denser entries — more history remembered per token of summary, but
a bigger drop in verbatim history each time one runs. If a chat holds fewer
messages than the batch size, Cozy retires what it can and always keeps the
newest message.

Summarization sends old chat content to the selected LLM server and may add API
cost or local processing time.

## Growing and rolling off

Folding messages in is always additive. Each update appends one new entry to the
story and leaves every existing entry untouched, so nothing you have already read
is silently rewritten. Entries are kept in chronological order, oldest first, and
the summary tells the model so.

Each story entry is labelled with the messages it covers, so you can see exactly
what any line came from.

The summary is a rolling window. When it reaches its size cap the **oldest**
entries roll off to make room for new ones. Nothing is compressed or merged — an
entry is either there in full or gone. The one exception is a section down to its
last entry: rather than empty the section, Cozy trims that entry's tail and marks
it with an ellipsis.

That means the summary remembers the recent past, not the whole chat. For
anything that must always be remembered — a premise, a standing rule, a fact
established in chapter one — write it in the **Author's Note** above the summary
card. The Author's Note is sent verbatim on every request and never rolls off.

Relationships are tracked separately under **Bonds**, which are updated in place
as they develop rather than appended to the timeline. When bonds run out of room,
the most recently opened one is dropped first, on the reasoning that a long-running
relationship carries more history than one opened a batch ago.

## Enable for a chat

1. Open the chat.
2. Open the memory panel beside the chat input.
3. Enable **Auto Summary**.

All existing old messages are processed in sequential batches as soon as Auto
Summary is enabled. Sending a new message may wait for an active update so that
no old history is skipped.

## Stop, rebuild, and reset

Three buttons sit in the **Auto Summary** header, left of the enable switch. None
of them changes your chat messages.

### Stop

Enabled only while a run is in progress. Use it when a long backfill —
"Summarizing… (batch 1/20)" — is more than you want to wait for.

**Every batch already finished is kept.** Cozy saves after each one, so stopping
at batch 7 of 20 keeps those seven entries and remembers how far it got. Only the
batch actually in flight is thrown away. Stopping takes effect at the next batch
boundary, so a request already sent to the summarizer finishes first.

Nothing is lost by stopping — the remaining messages are simply still waiting,
and the status line says how many.

### Rebuild — or continue

The same button does one of two things, and its tooltip tells you which:

- **Continue summarizing** when a summary already has entries and some history is
  still unsummarized — after stopping a run, or after closing the browser mid-run.
  It picks up from where it left off instead of redoing finished batches.
- **Rebuild from history** when there is nothing to continue: it discards the
  summary and generates it again from the stored messages, one entry per batch.

You rarely need to press it. Sending a message or finishing a turn also continues
an interrupted run, because the pre-send check folds in any history that has aged
out but is not yet summarized.

To force a complete regeneration when a summary is only partly built — say you
edited an old message *and* a run was interrupted — **Reset first, then rebuild**.
Rebuild on its own would continue rather than start over, leaving the stale entry
in place.

### Reset

Clears the current summary and forgets how far summarization got. No LLM call.

Reset then rebuild is the from-scratch path. Plain rebuild is safer when you just
want a regeneration: it keeps the existing summary usable until the new one is
complete, so a failure part way leaves you with the old one rather than nothing.

Reset then rebuild is also how you convert a summary created by an older version
of Cozy, which produced several entries per batch, into the current
one-entry-per-batch form.

## Limits

- Summary quality depends on the selected model.
- Old entries are dropped, not condensed, once the cap is reached — use the
  Author's Note for anything that must survive.
- The summary uses part of the model's context budget.
- Editing an already summarized message does not automatically rewrite the
  summary.
- Stopping a run takes effect between batches, not instantly: a request already
  sent to the summarizer runs to completion first.
