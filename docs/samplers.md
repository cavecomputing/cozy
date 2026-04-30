# Sampler Settings

When an LLM generates text, it doesn't just pick the single "best" next word every time. Instead, it produces a list of possible next words, each with a probability score. **Samplers** are the rules that decide which word actually gets chosen from that list. Different sampler settings let you control how creative, repetitive, or focused the output is.

You can enable or disable any sampler group from the gear icon in the Sampler section header of the Settings panel. Only enabled samplers are sent to the backend.

---

## Temperature

**What it does:** Controls how "random" the word choices are. Think of it like a creativity dial.

- **Low values (0.1–0.5):** The model plays it safe, picking the most likely words. Output is predictable and consistent.
- **Medium values (0.7–1.0):** A balanced mix of predictability and creativity. Good for most use cases.
- **High values (1.2–2.0+):** The model takes more risks, choosing less obvious words. Output gets more creative but can become incoherent at extreme values.
- **0:** The model always picks the single most likely word (deterministic/greedy).

| Setting | Default | Range |
|---------|---------|-------|
| Temperature | 1.0 | 0–5 |

---

## Dynamic Temperature

**What it does:** Instead of using a fixed temperature, this automatically adjusts the temperature based on how "confident" the model is about each word. When the model is very sure about the next word, temperature stays low. When there are many plausible options, temperature increases.

This can give you the best of both worlds — consistent output when the answer is obvious, creative output when there's room for variation.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Range | 0 | 0–5 | How much the temperature can vary. 0 = disabled. The final temperature will be within [temperature - range, temperature + range] |
| Exponent | 1.0 | 0–10 | Controls the curve of the adjustment. Higher values make the dynamic scaling more aggressive |

---

## Top-P (Nucleus Sampling)

**What it does:** Instead of considering every possible word, Top-P only looks at the smallest set of words whose combined probability adds up to the P value.

For example, if Top-P is 0.9, the model finds the fewest words that together have a 90% chance of being chosen, and picks from only those. This cuts off the long tail of unlikely words while still allowing some variety.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Top-P | 1.0 | 0–1 | 1.0 = disabled (consider all words) |

---

## Top-K

**What it does:** Limits the model to choosing from only the K most likely words. Simple and effective.

For example, Top-K of 40 means the model only considers the 40 most probable next words, ignoring everything else.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Top-K | 0 | 0–500 | 0 = disabled (no limit) |

---

## Min-P

**What it does:** Removes any word whose probability is less than a fraction of the most likely word's probability.

For example, if Min-P is 0.05 and the top word has a 60% probability, any word with less than 3% probability (60% × 0.05) gets eliminated. This adapts naturally — when the model is confident, fewer words survive; when it's uncertain, more remain.

Min-P is often considered a more intuitive alternative to Top-P and Top-K.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Min-P | 0 | 0–1 | 0 = disabled |

---

## Typical-P (Locally Typical Sampling)

**What it does:** Selects words that are "typically surprising" — not too predictable, not too random. It measures how close each word's probability is to what you'd statistically expect, and keeps only those within a typical range.

This tends to produce text that reads more naturally, avoiding both the boringly obvious and the wildly improbable.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Typical-P | 1.0 | 0–1 | 1.0 = disabled |

---

## Top-N Sigma

**What it does:** Filters words based on how many standard deviations they are from the mean probability. Words that are statistical outliers (too unlikely) get removed.

This is a mathematically principled way to trim the word list that adapts to the shape of the probability distribution.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Top-N Sigma | -1 | -1–10 | -1 = disabled |

---

## TFS-Z (Tail-Free Sampling)

**What it does:** Removes words from the "tail" of the probability distribution — the long trail of very unlikely words. It does this by looking at the rate of change (second derivative) of the sorted probabilities and cutting off where the curve flattens out.

The idea is that the tail words contribute almost nothing meaningful and can only introduce noise.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| TFS-Z | 1.0 | 0–1 | 1.0 = disabled. Lower values trim more aggressively |

---

## Repetition Penalty

**What it does:** Makes the model less likely to repeat words it has recently used. Each time a word appears in the recent context, its probability of being chosen again gets reduced.

This is useful for preventing the model from getting stuck in loops or repeating the same phrases.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Penalty | 1.0 | 0–3 | 1.0 = no penalty. Higher values penalize repetition more strongly |
| Last N Tokens | 64 | -1–4096 | How far back to look for repetitions. -1 = entire context, 0 = disabled |

---

## Presence Penalty

**What it does:** Penalizes words based on whether they've appeared at all in the text so far. Unlike repetition penalty, it doesn't care *how many times* a word appeared — just whether it appeared at least once.

This encourages the model to talk about new topics rather than revisiting the same ones.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Presence Penalty | 0 | -2–2 | 0 = disabled. Positive values discourage reuse, negative values encourage it |

---

## Frequency Penalty

**What it does:** Penalizes words proportionally to how many times they've already appeared. A word used 5 times gets penalized 5× more than a word used once.

This is stronger than presence penalty for heavily repeated words but gentler for words that only appeared once or twice.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Frequency Penalty | 0 | -2–2 | 0 = disabled. Positive values discourage reuse, negative values encourage it |

---

## DRY (Don't Repeat Yourself)

**What it does:** Detects and penalizes repeated *sequences* of words, not just individual words. If the model starts generating a phrase it has already produced, DRY applies an exponentially increasing penalty to discourage continuing the repetition.

This is more sophisticated than simple repetition penalty because it catches repeated sentences, paragraphs, or patterns — not just repeated single words.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Multiplier | 0 | 0–5 | Strength of the penalty. 0 = disabled. Typical value: ~0.8 |
| Base | 1.75 | 0–5 | Base of the exponential penalty. Higher = harsher penalty for longer repetitions |
| Allowed Length | 2 | 0–100 | Repetitions this short or shorter are not penalized. Prevents penalizing common short phrases like "the", "it is", etc. |
| Penalty Last N | -1 | -1–4096 | How far back to scan for repeated sequences. -1 = entire context, 0 = disabled |

---

## Mirostat

**What it does:** A fundamentally different approach to sampling. Instead of filtering the word list, Mirostat tries to maintain a target level of "surprise" (perplexity) in the output. It dynamically adjusts its behavior to keep the text at a consistent quality level.

Think of it like cruise control for creativity — you set your desired level and Mirostat adjusts on the fly to maintain it.

When Mirostat is enabled, most other sampling methods (Top-P, Top-K, etc.) are typically bypassed.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Mode | 0 | 0, 1, 2 | 0 = disabled, 1 = Mirostat v1, 2 = Mirostat v2 (generally preferred) |
| Tau | 5.0 | 0–20 | Target surprise level. Lower = more focused/coherent, higher = more creative/diverse |
| Eta | 0.1 | 0–1 | Learning rate — how quickly Mirostat adapts. Higher = faster adjustment but less stable |

---

## XTC (eXclude Top Choices)

**What it does:** Randomly removes the most probable words, forcing the model to pick from less obvious alternatives. This is intentionally counterintuitive — by sometimes excluding the "best" choices, you get more diverse and surprising output.

Each generation step, there's a random chance (set by Probability) that high-probability words (above the Threshold) will be excluded.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Probability | 0 | 0–1 | Chance that XTC activates on each word. 0 = disabled |
| Threshold | 0.1 | 0–1 | Words above this probability are eligible for exclusion. Values above 0.5 effectively disable XTC |

---

## Max Tokens

**What it does:** Sets a hard limit on how many tokens (roughly words/word-pieces) the model can generate in a single response. Once this limit is reached, generation stops.

This is not a sampling method per se, but a generation control. Useful for keeping responses concise or managing costs/time.

| Setting | Default | Range |
|---------|---------|-------|
| Max Tokens | 512 | 1–32768 |

---

## Seed

**What it does:** Sets the random number generator seed. Using the same seed with the same input and settings should produce the same output, making generation reproducible.

Useful for testing or when you want to regenerate the exact same response.

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Seed | -1 | -1–999999999 | -1 = random seed each time |

---

## How Samplers Work Together

Samplers are applied in a chain, one after another. The default order in llama.cpp is:

```
penalties → dry → top_n_sigma → top_k → typical_p → top_p → min_p → xtc → temperature
```

Each step narrows down or reshapes the list of candidate words before the final selection. This means:

1. **Penalties** are applied first, adjusting probabilities based on what's already been generated
2. **Filtering samplers** (Top-K, Top-P, Min-P, etc.) remove unlikely candidates
3. **Temperature** is applied last, scaling the final probabilities before the random choice

You don't need to use all samplers at once. A common minimal setup is just **Temperature + Min-P**, or **Temperature + Top-P + Top-K**. Experiment to find what works best for your use case.

---

## Common Presets

Below are some starting-point configurations for different use cases. These are not hard rules — they're common defaults you'll see across the community that you can use as a baseline and tweak from there.

### General Chat / Roleplay

A balanced setup that produces varied, natural-sounding text without going off the rails.

| Sampler | Value | Why |
|---------|-------|-----|
| Temperature | 0.7–0.9 | Enough creativity for engaging responses without becoming incoherent |
| Min-P | 0.05–0.1 | Trims the junk words while keeping interesting options on the table |
| Rep. Penalty | 1.05–1.15 | Light touch to prevent obvious loops without making the text feel stilted |
| Last N Tokens | 256–512 | Looks far enough back to catch repeated patterns across several paragraphs |
| Max Tokens | 1024–2048 | Enough room for detailed responses |

### Creative Writing

Prioritizes variety and expressiveness. Good for stories, poetry, or brainstorming.

| Sampler | Value | Why |
|---------|-------|-----|
| Temperature | 1.0–1.3 | Higher creativity — the model reaches for less obvious word choices |
| Min-P | 0.02–0.05 | Very permissive — lets unusual but interesting words through |
| Top-P | 0.95 | Gentle safety net to prevent total nonsense |
| Rep. Penalty | 1.1–1.2 | Keeps prose from repeating itself |
| Last N Tokens | 512 | Wide lookback for consistency across longer passages |
| DRY Multiplier | 0.8 | Prevents repeated phrases and sentence structures |
| DRY Allowed Length | 2 | Only penalizes repeated sequences longer than 2 tokens |
| Max Tokens | 2048–4096 | Room for long-form output |

### Deterministic / Factual

When you want consistent, predictable output — instructions, code, factual Q&A.

| Sampler | Value | Why |
|---------|-------|-----|
| Temperature | 0–0.3 | Very low randomness — picks the most likely words |
| Top-K | 20–40 | Hard limit on candidates to prevent surprises |
| Top-P | 0.9 | Backup filter |
| Rep. Penalty | 1.0 | No penalty needed — factual text naturally varies less |
| Max Tokens | 512–1024 | Factual responses are usually shorter |

### Mirostat (Set-and-Forget)

If you don't want to fuss with multiple samplers, Mirostat handles quality control automatically. Disable other filtering samplers when using this.

| Sampler | Value | Why |
|---------|-------|-----|
| Mirostat Mode | 2 | Mirostat v2 is more stable and generally preferred |
| Tau | 3.0–5.0 | 3.0 for focused output, 5.0 for more creative output |
| Eta | 0.1 | Default learning rate works well for most cases |
| Temperature | 0.8 | Still useful as a baseline even with Mirostat |
| Max Tokens | 1024–2048 | Adjust to taste |

### Anti-Repetition (Heavy)

When you're dealing with a model that tends to loop or repeat itself heavily.

| Sampler | Value | Why |
|---------|-------|-----|
| Temperature | 0.8 | Moderate creativity |
| Min-P | 0.05 | Standard filtering |
| Rep. Penalty | 1.2–1.3 | Strong word-level repetition penalty |
| Last N Tokens | 512–1024 | Wide lookback window |
| Presence Penalty | 0.3–0.6 | Discourages revisiting the same topics |
| Frequency Penalty | 0.3–0.5 | Progressively penalizes overused words |
| DRY Multiplier | 0.8–1.0 | Catches repeated phrases and sentences |
| DRY Allowed Length | 2 | Ignores trivially short repetitions |
| DRY Penalty Last N | -1 | Scans the entire context |
| Max Tokens | 1024 | Standard length |
