# Auto Summaries — Design Spec

> Design/intent document for the "Auto Summaries" feature. This describes **what** it should do and **how it should behave**, not the code. Claude Code will use this to plan the implementation.

## Problem

In long roleplays, the oldest messages fall out of the context window and are gone permanently — the AI stops remembering early events, relationships, and promises. Today the app simply drops old messages (see `selectContextMessages` in `static/js/tokenizer.js`, used by `buildChatPayload` in `static/js/request-builder.js`). There is no memory of anything that ages out.

## Goal

Give a chat a **cumulative running summary** of everything that has aged out, so the AI keeps remembering old events — especially **character relationships and how they were built over time** — without keeping the raw messages in context.

The feature is **optional and per-chat-aware**. When disabled, the app behaves exactly as it does today.

## Non-goals (for the first version)

- No hand-editing of the generated summary text (only pinning — see below).
- No re-summarizing when a already-summarized message is later edited/swiped (named as a known limitation).
- No vector search / RAG. This doc is summary-only.

---

## Core concept

Think of the chat in three zones:

1. **Summarized (old):** already folded into the running summary.
2. **Waiting (aging out):** messages that have just left the recent window but have
   not yet been folded into the summary. A generation request waits for this zone to
   be retired before it is sent.
3. **Recent (live):** newest messages, always sent word-for-word.

New messages enter at the Recent end and push older ones toward Waiting. As soon as
history crosses that boundary, a background job starts folding it into the running
summary. If generation catches the job in flight, a preflight barrier waits for the
summary watermark to reach the raw-context boundary. The running summary is then
injected into the prompt (like the author's note is), so the AI sees a compressed
"story so far" plus the recent messages in full.

### Chunked back-fills, with a no-gap send barrier

Large backlogs are processed in bounded calls:

- A run begins when at least one message has crossed the raw-context boundary.
- Each summarizer call folds in at most the configured number of messages.
- Existing-chat back-fills and other accumulated work are split across as many calls
  as necessary, checkpointing completed batches.

The send barrier, rather than deliberate overfilling of the model context, guarantees
there is **never a memory gap**: generation does not proceed while an aged-out message
is in neither the persisted summary nor the selected raw history.

### The summary is cumulative

Each summarize call receives:

1. The **previous summary**
2. The **new batch** of messages being folded in
3. **Instructions** on what to keep/drop (see "Summarizer instructions")

…and returns one updated summary that covers **everything old so far** — not just the latest batch. It grows like a snowball: each batch it rolls over gets packed on.

At prompt-build time the AI sees: **running summary (all old history, compressed) + recent messages (full text)**. The two meet in the middle with nothing missing.

---

## The summary object

The summary is **not a single blob of prose** — it is a **list of discrete short lines**, so individual lines can be pinned. It has two parts:

- **Story so far:** a handful of bulleted plot beats (the spine of what happened).
- **Bonds:** one line per important relationship — how the characters stand now, and the key shared moment behind it.

Example (illustrative):

```
STORY SO FAR
- Nell pulled Luna from the sea during a storm after the Marigold was sunk by the Navy.
- They sailed the Widow's Teeth and learned Commodore Vance caused the Mercy Bell wreck.
- Reunited Luna with her sister Cerina in Tortuga; escaped aboard the Raven.

BONDS
- Luna & Nell: deep trust, turning romantic. Began when Nell saved her drowning. Nell — walled off for 3 years — has let Luna in.
- Luna & Cerina: fierce sisterly love. Believing Luna dead, Cerina barricaded herself in a tavern cellar rigged with gunpowder, meaning to kill Vance's men and die with them — grief-driven vengeance, not cornered helplessness. Luna being alive pulled her back.
- Cerina & Sable: old flame + betrayal (Sable took Cerina's eye); rekindled, volatile.
```

Every line is individually pinnable.

### Principle: preserve context, not just facts

The single most important quality of a good line is that it keeps the **why** — motivation, emotional stakes, and circumstances — not just the bare event. A compressed line shades how the LLM recalls the moment forever, so flattening changes the character.

- ❌ *"Cerina nearly died believing Luna dead."* (flat — reads as passive misfortune)
- ✅ *"Believing Luna dead, Cerina rigged a cellar to blow and meant to take Vance's men down with her — grief-driven vengeance."* (keeps the intent and emotional truth)

When compressing, shed **length**, not **meaning**. It is better to keep fewer events with their real context than many events reduced to hollow one-liners. This applies to both the normal fold-in and the tighten-up pass.

---

## Size cap (percentage of context)

The summary is capped at a **percentage of the context window**, configurable (default **10%**). Example: context = 32768 → cap ≈ 3276 tokens.

- After folding in a batch, the app checks the summary's length.
- **Under the cap:** nothing extra happens.
- **Over the cap:** run a "tighten-up" pass that rewrites the summary shorter, guided by the priority order below.

**Budget note:** the summary shares the context window with everything else (character info, lorebook, author's note, recent messages, and reply space). When enabled, the summary's slice reduces the room left for recent messages. This is an intended trade: spend a little "recent detail" to buy "never forgets the old stuff."

### Compression priority (what survives a tighten-up)

When forced to compress, keep in this order:

1. **Bonds / relationships** and the specific moments that formed them (top priority — this is the whole point of the feature).
2. **Unresolved threads / promises / debts.**
3. **Plot events** as the *reason* for a bond, not for their own sake.
4. Generic action and small talk get squeezed or dropped first.

Plus a **recency lean:** recent events stay more detailed; ancient history gets boiled down.

Honest caveat: this is the model's judgment, not an exact rule — good, not perfect. Pinning (below) is the safety net.

---

## Pinning

Pins let the user protect specific generated lines from being reworded or dropped.

- A pin is **a line lifted from the generated summary**, locked by the user. (Pins are **not** user-written free text — that is what the author's note is for.)
- **Pinned lines are kept word-for-word** through every future fold-in and tighten-up.
- **Unpinned lines** can be reworded, merged, or dropped as needed.
- **Pins count *inside* the summary's percentage budget.** The more you pin, the harder the auto-summary compresses to fit alongside them. This keeps total size predictable and self-regulating.
- **Edge case:** if pins alone exceed the cap, handle gracefully — warn the user and/or let pins win and skip the auto portion.

---

## Coverage bookkeeping

Each chat stores a monotonically increasing `summary_up_to_msg_id` watermark. Messages
in that chat at or below the watermark are considered **folded into the summary**.

- Prevents double-counting messages across summarize runs.
- Advances after completed normal batches and once at the end of an atomic rebuild.
- Lets the UI draw the boundary between "summarized history" and "live messages."

---

## Enabling on an existing chat (bulk back-fill)

When the feature is turned on for a chat that didn't have it:

- Everything **older than the current recent window** gets summarized to seed the "story so far."
- For large backlogs, do **not** do it in one giant call — run it through the **same batching**, in several passes, folding progressively. (Reuses the normal machinery; avoids a single huge/slow/expensive call.)
- Each message the back-fill covers is **tagged** as summarized.

---

## Storage

Summaries and their state are **tied to the individual character-chat**, the same way the author's note is (`author_note` column on the `chats` table in `shared.py`). Per-chat data likely needs:

- The current summary (its list of lines, distinguishing pinned vs. auto).
- Which pins are set.
- The "summarized up to" bookkeeping (via per-message tags and/or a watermark).

*(Exact schema is Claude Code's call.)*

---

## Settings — two separate areas

### 1. Global: new "Auto Summaries" settings tab

- Enable / disable the feature.
- **Summarizer model config** — optional endpoint / API key / model overrides for
  using a cheaper summarizer; each blank field falls back to the main API connection.
- Summary size cap as a **percentage of context** (default 10%).
- **Messages per summarizer batch** — the maximum backlog folded into one model call.
- (Any other knobs that emerge during planning.)

### 2. Per-chat: "memory management" button in the chat input bar

- A section showing **this chat's** current summarized lines.
- Pin / unpin individual lines.
- Read-only summary text (no direct editing — pinning only).

---

## Summarizer instructions (the prompt given to the summarizer model)

Rough intent for the instruction text (to be refined during tuning):

- You are updating a running summary of a roleplay. Blend the previous summary and the new messages into one updated summary.
- Output **discrete short lines**, under two headings: **Story so far** and **Bonds**.
- Prioritize **relationships and the moments that formed them**, then unresolved threads/promises, then plot. De-prioritize small talk and moment-to-moment action.
- **Preserve the *why*, not just the *what*.** Keep motivation, emotional stakes, and circumstances. Shed length, never meaning — fewer events with their real context beat many hollow one-liners.
- Keep **pinned lines exactly as written**; you may rewrite unpinned lines.
- Stay under the size budget provided; if over, compress the lowest-priority, oldest material first.

---

## Known limitations (accepted for v1)

- Editing, deleting, or swiping a message **after** it has aged out (including while
  its summarizer batch is in flight) will not retroactively fix the summary. Unpin
  any now-stale protected lines, then use **Rebuild from history** (or **Reset** for
  a completely clean slate) after changing older history.
- Compression is model judgment, not exact — pins are the mitigation.
- Summary quality depends on the chosen summarizer model.

---

## Implementation hints (Claude Code to plan the real work)

Light pointers only — not a plan:

- The default injected block follows the **author's note pattern**: a per-chat field →
  a key in the `ctx` object → a `{{#summary}}…{{/summary}}` block in
  `DEFAULT_PROMPT_TEMPLATE` (`shared.py`), assembled in `buildChatPayload`
  (`static/js/request-builder.js`). Runtime request building also supplies a fallback
  memory block for custom templates that omit `{{summary}}`.
- The context boundary (which messages are about to age out) is already computed by `getContextBoundaryMsgId` in `static/js/tokenizer.js` — useful as the trigger signal.
- The summarizer call is a **non-streaming** LLM request; the existing proxy pattern in `routes/llm.py` (see `test_llm`) is the reference for a background call to a configurable endpoint.
- Keep volatile blocks (summary) positioned so they don't needlessly break any prompt-prefix caching.
