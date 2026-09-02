# BigBear preset provenance

The four `BigBear - *.json` presets in this directory are a derivative of the **Writer's Block v5**
SillyTavern preset by **Deiomo**. The prose in them is overwhelmingly the upstream author's,
verbatim; the *configuration* is not, and neither are four fragments that were rewritten. This file
records exactly what was taken and what was changed, so the derivation stays auditable now that the
build script that produced it is gone.

Credit lives in the `## Acknowledgements` section of [README.md](../README.md) and on the About page
in the app. This file is the mechanical record behind that credit, not a second acknowledgement.

## These files are now the source of truth

The presets were generated once, by a script that consumed a SillyTavern preset export that has
never been part of this repository. That script has been deleted: nobody else could run it, and a
build step that cannot be reproduced is worse than no build step. The JSON in this directory is the
artifact now. Edit it directly.

Shipping a revised preset still means **adding a file**, never editing one — the filename minus
`.json` is the title each preset is seeded under, and `seed_default_prompts()` skips a title that
already exists so user edits survive. See the prompt section of [docs/db.md](../docs/db.md).

## Why a separate name

SillyTavern presets are a flat pool of ~125 prompt fragments plus a per-character `prompt_order`
saying which are enabled and in what sequence. Cozy has no equivalent toggle system — a preset here
is one `system_prompts` row holding two templates: `content`, the leading system message, and
`post_history_content`, a trailing user message. Each BigBear preset flattens one point in that
toggle space into that pair.

Upstream ships Active Persona with Adaptive Blitz, which renders the player's stated intent and
stops. BigBear runs Director Mode with Adaptive Novel, which takes the player's line as a stage
direction and writes the scene out, protagonist included. That is different behavior, and naming it
after Writer's Block would put the author's name on a choice they did not make.

## What was taken

Seventeen fragments, copied byte-for-byte except where noted below. Fragment names are matched with
surrounding whitespace stripped — several carry a trailing space in the export.

**System template** (`content`), in order:

1. `De-Positivity`
2. One prose style per variation (see below)
3. `Narrative Core`, `Character Architecture`, `Dialogue Rules`, `Anti-Resolution`,
   `Explicit Content Instructions`, `Dynamic Sentence Structure`
4. `3rd Person Omniscient`
5. *Cozy card block* — lorebook, persona, description, personality, scenario
6. `Enhance Definitions`
7. *Cozy tail block* — character instructions, example dialogue
8. `🌍 Enhanced World🌎`, `👫Better Side Characters `,
   `🐱Anthropomorphic Realism 🐶` — the emoji and trailing spaces are part
   of the upstream names
9. *Cozy closing block* — author's note, running summary, post-history instructions

**Post-history template** (`post_history_content`), in order:

1. *Cozy direction wrapper* (see below)
2. `Adaptive Novel` — rewritten
3. `ANTI-SLOP`
4. `Director Mode` — rewritten
5. `Bare Essentials COT` — rewritten

Placement follows the export's own metadata. Fragments with `injection_position: 0` are sequenced by
`prompt_order` and land in the system template in that relative order; `injection_position: 1` means
absolute-depth injection after the chat history, so those land in the post-history template. Higher
depth stacks earlier, which is why pacing (depth 2) precedes the depth-0 fragments.

The Cozy blocks sit where the export puts the equivalent SillyTavern markers: world info and card
fields after the POV block, add-ons after the card data, post-history instructions last. Author's
note and the running summary have no SillyTavern counterpart and sit late, where recency helps.

The Enhanced World add-on is not really optional. Every CoT variant instructs the model to pull
rules from `<world_and_context>`, and that tag exists only inside that fragment.

## The four variations

| Preset | Prose style fragment | Pacing |
|---|---|---|
| `BigBear - General` | `General Purpose` | standard |
| `BigBear - Light Novel` | `Light Novel/Anime Author` | retiered for dense whitespace |
| `BigBear - Grimdark` | `Joe Abercrombie (Grimdark Comedy)` | standard |
| `BigBear - Smut` | `Hentai Author` | standard |

Each style instructs interiority directly — `# INTERIORITY` in the first three, `## INTERNAL VOICE`
in Hentai Author — so the model inhabits `{{user}}` without further prompting.

## What was dropped

Two of the export's styles were dropped rather than edited, because both carry their own
turn-taking rules that fight the Director/Novel chassis:

- **Conversational Roleplay** — "Mirror their pacing and length", "pass the momentum back to
  `{{user}}`".
- **John Steinbeck** — a wide observational camera with withheld interiority, which leaves the
  protagonist un-driven.

## What was rewritten, and why

Each rewrite exists because a fragment assumes something SillyTavern provides and Cozy does not.

### `Director Mode`

Two assumptions do not hold here.

First, the fragment treats `{{user}}` as the director — a voice outside the story with no body.
Cozy resolves `{{user}}` to the **persona name**, which is the protagonist, so the original line
reads as an order never to describe the protagonist. Director and protagonist had to be named
separately.

Second, *"Stop writing immediately if the protagonist's reaction is required"* contradicts the same
fragment's *"You control ALL characters, including the Protagonist"*, and is the specific
instruction that turns a novel into a turn-taking exchange. Lifting the stop was not enough — the
replacement has to say the direction is a *starting point*, or the model renders the directed beat,
treats the instruction as discharged, and ends there anyway. That also overrides Narrative Core's
"Cut mid-action or mid-thought", which is scene-break craft advice the model otherwise reads as
leave to stop early; this fragment wins on recency, sitting in post-history.

### `Adaptive Novel`

The fragment is built for an exchange: four clauses end the response on an opening for `{{user}}`
and forbid moving past their input, and rule 1's "Stay locked in the current minute" blocks the
multi-beat continuation Director Mode asks for just as effectively. Those clauses were removed.

What survives — and the reason for preferring this fragment over Epic Mode — is the paragraph budget
tiered by beat weight, which stops a quiet scene from being padded out to the same length as a
climax. Epic Mode's flat "12+ paragraphs" cannot do that.

The header rewrite also settles a conflict the original does not have: "never fast-forward plot"
would otherwise refuse a direction like Director Mode's own "Skip to the next morning" example.

### `Adaptive Novel`, Light Novel variant

The tiers are measured in paragraphs. Light Novel prose sets "one action/thought/line per paragraph.
Break often", so a twelve-paragraph response can be twelve single lines — the budget reads as
satisfied by something very short. The tiers were recounted in **beats**, which decouples them from
the line breaks without touching the style's signature whitespace:

| Original | Retiered |
|---|---|
| Climactic (13–15+ paragraphs) | Climactic (6–8 beats) |
| Developmental (10–12 paragraphs) | Developmental (4–6 beats) |
| Transitional (5–6 paragraphs) | Transitional (2–3 beats) |
| Reactive (3–4 paragraphs) | Reactive (1–2 beats) |

The beat numbers are well below the paragraph numbers they replace. Beats are plot movement, so
matching them one-for-one would buy length by racing the story forward; the length is meant to come
from expanding each beat.

### `Bare Essentials COT`

Two artifacts do not survive the port:

- The `(tracker all the way at the end)` line asks for a status block these presets deliberately
  leave out.
- The outer `<cot>` wrapper is redundant with the `<think>` tags nested inside it.
  [thinking.js](../static/js/thinking.js) keys on `<think>` alone (`THINKING_TAG_PAIRS`), so a model
  that echoed the wrapper would print literal `<cot>` tags into the message body.

## The direction wrapper

SillyTavern injects the post-history fragments as **system** messages at depth 0. Cozy has no depth
injection: `post_history_content` renders as a **user** message, and `enforceTrackedAlternation`
then merges it into the player's own turn. Without a delimiter the model cannot tell where the
player's text stops and the directives begin — which breaks every fragment defined in terms of
"`{{user}}`'s input". The wrapper restores that boundary, and is the first thing in the post-history
template.
