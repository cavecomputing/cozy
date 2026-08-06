# Regex Output Filters

Regex filters are find-and-replace rules that clean up a character's replies —
straightening quotation marks, deleting out-of-character asides, collapsing
runs of blank lines.

No filtering happens until you select a preset. Cozy ships several presets, but
none of them is active on a fresh install.

## What filters touch

Filters run once, after the model finishes and before the message is saved. The
result is what you see, what is stored, and what the model reads back next turn.

They apply to **character replies only** — including regenerations and swipes.
Your own messages, the character's greeting, and messages already in the chat
are never rewritten.

There is no undo. A filter rewrites the saved message, so use the **Test** box
to check a pattern before it runs on a real reply.

Ticking **Display only** on a filter changes all of that — see below.

## Display-only filters

Each filter has a **Display only** checkbox. With it on, the filter moves to
render time: it rewrites the message on screen and nothing else. The stored
message keeps the model's original words, so that is what the edit box shows,
what is exported, and what goes back to the model next turn.

Use it for rules that build something to *look at* rather than clean up text —
turning a line of stats into an HTML card, a number into a progress bar. Cozy
renders HTML in messages (sanitised: no `<script>`, no event handlers), so a
filter can produce real markup.

Doing that with an ordinary filter stores the markup, which costs context on
every later turn and shows the model an example to copy — replies start arriving
with hand-written HTML in them. A display-only filter avoids both, because the
history never sees it.

Two details worth knowing:

- Display-only filters run on **every** character message each time it is drawn,
  including the greeting and old messages from before you wrote the rule.
  Ordinary filters only ever run on a reply as it arrives.
- They re-run on each token while a reply streams in. A pattern anchored to a
  whole line simply won't match until that line finishes arriving.

A filter is in one group or the other, never both: an ordinary filter never runs
at render time, and a display-only one never touches what is saved.

## Presets

Open **Settings → Regex**. The dropdown at the top selects the active preset:

- Select a preset to make its filters live.
- Select **None** to switch filtering off. This is the only off switch — there
  is no per-filter enable toggle.
- **+** creates an empty preset, and the trash icon deletes the selected one.

Only one preset is active at a time. Deleting the active preset turns filtering
off rather than falling back to another one.

### Bundled presets

Three presets ship with Cozy, aimed at models that write dialogue with
non-English quotation marks:

- **German punctuation** — rewrites `„…“` pairs and inward guillemets `»…«` as
  straight quotes, then mops up any stray curly mark.
- **French punctuation** — rewrites outward guillemets `«…»` as straight quotes,
  including the no-break spaces French pads them with, and removes the space
  French puts before `;` `:` `!` `?`.
- **Straighten all quote marks** — all of the above plus curly single quotes and
  apostrophes. Kept separate because rewriting apostrophes is not always wanted.

These are ordinary user data once seeded. Edit or delete them freely; Cozy will
not put them back.

You may not need them. Cozy's renderer already recognises German, French and
Japanese quotation marks as speech and styles them as dialogue, keeping whatever
marks the reply actually used — see [Marks Cozy already understands](#marks-cozy-already-understands)
below. Reach for a preset when you want the punctuation itself changed.

## Filters

Each filter has an optional **name**, a **Find** pattern, a **Replace** value,
**flags**, and the **Display only** switch. Filters run top to bottom, and each
one sees the previous one's output — so a rule that mops up leftovers belongs
last.

A filter is live whenever its Find pattern compiles. A half-typed pattern that
doesn't compile is skipped and flagged rather than blocking the send, and a
filter that would blank the reply entirely is ignored.

The in-app help (the **?** icon in the Regex header) is the full syntax
reference: building blocks, flags, and worked examples. In short:

| Flag | Effect |
|------|--------|
| `g`  | Replace every match, not just the first. On by default for new filters. |
| `i`  | Ignore case. |
| `m`  | `^` and `$` match the start and end of each line. |
| `s`  | `.` matches line breaks too. |

In **Replace**, `$1` and `$2` insert what the first and second `(…)` group
captured, `$&` inserts the whole match, `$$` inserts a literal `$`, and an empty
Replace deletes whatever Find matched.

### Newlines and escapes

Find and Replace are single-line inputs, so a real line break cannot be typed
into either. Write `\n` (and `\t`) instead — it means the same thing in a Find
pattern, and the Replace field expands it when the filter runs. For a literal
backslash-n in the output, write `\\n`.

This matters for quote rules: a pattern that excludes `\n` stops a mismatched
quote from swallowing the following paragraph. Every bundled preset relies on it.

## Test box

Paste a sample reply into **Sample** and the **Result** box shows what the
preset's ordinary filters would do to it. The preview runs the same matcher the
real save path does, so what it shows is what gets stored.

If the preset contains a display-only filter, a second box — **As displayed** —
appears underneath, showing the Result run through those as well. That is what
the message bubble would end up looking like. The gap between the two boxes is
the point: everything below **Result** exists only on screen.

## Import and export

The import/export icon beside the preset controls reads and writes JSON.

Import accepts:

- A Cozy export (`{"name": …, "filters": […]}`).
- A SillyTavern regex script, or a list of them.

SillyTavern scripts are converted on the way in. Slash-form patterns like
`/pattern/gi` are split into the pattern and its flags, multi-line replacements
are converted to `\n` escapes, and a script marked *Alter Chat Display* in
SillyTavern arrives as a display-only filter. Two cases are reported after an
import:

- Scripts disabled in SillyTavern are skipped.
- Scripts targeting anything other than AI output are imported anyway, and will
  apply to character replies — the only placement Cozy has.

Pasting a slash-form pattern such as `/„([^"]*)"/g` straight into **Find** also
works: the pattern lands in the box and the matching flag boxes tick themselves.

An imported preset whose name is already taken gets a numbered suffix rather
than overwriting the existing one.

## Marks Cozy already understands

Independently of filters, Cozy's renderer treats these pairs as quoted speech
and styles them as dialogue:

```text
"…"    straight
“…”    English curly
„…“    German (also accepts „…” )
»…«    German/Danish guillemets
«…»    French/Swiss/Russian guillemets
「…」   Japanese corner brackets
『…』   Japanese white corner brackets
```

The reply keeps the marks it was written with — Cozy does not anglicise them.
Single quotes are deliberately excluded, because `'` doubles as an apostrophe.

Converting punctuation is the Regex tab's job, and only when you ask for it.
