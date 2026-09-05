# Sampler Settings

Samplers change how an LLM chooses its next token. They can make replies more
predictable, varied, or resistant to repetition.

Cozy sends enabled sampler values to the configured LLM server. The server
decides how to apply them. Unsupported settings may be ignored or rejected.

Start with the defaults. Change one setting at a time.

## Core settings

| Setting | Default | What it changes |
|---|---:|---|
| Temperature | 1.0 | Higher values add variation. Lower values are more predictable. |
| Top-P | 0.95 | Limits choices to tokens inside a cumulative probability threshold. `1.0` disables the filter on most servers. |
| Top-K | 0 | Limits choices to the K most likely tokens. `0` usually disables it. |
| Min-P | 0.05 | Removes tokens that are too unlikely compared with the best token. `0` usually disables it. |
| Repetition penalty | 1.0 | Reduces repeated tokens. `1.0` disables the penalty. |
| Last N tokens | 64 | Controls how far back repetition detection looks. |
| Max response tokens | 512 | Maximum tokens the server may generate for one reply. |

## Advanced settings

Advanced samplers are hidden by default. Open **Settings → Samplers → Active
samplers** to enable them.

| Setting | Disabled value | Purpose |
|---|---:|---|
| Dynamic Temperature | Range `0` | Adjusts temperature during generation. |
| Typical-P | `1.0` | Keeps tokens with locally typical probability. |
| Top-N Sigma | `-1` | Filters low-scoring tokens using a sigma threshold. |
| Presence penalty | `0` | Discourages tokens that appeared at least once. |
| Frequency penalty | `0` | Discourages tokens based on how often they appeared. |
| DRY | Multiplier `0` | Penalizes repeated multi-token sequences. |
| Mirostat | Mode `0` | Adjusts sampling toward a target surprise level. |
| XTC | Probability `0` | Occasionally excludes high-probability choices. |
| Seed | `-1` | Requests a random seed. |

## Backend compatibility

Temperature, Top-P, presence penalty, frequency penalty, and maximum tokens are
common OpenAI-style parameters. Other settings are mainly supported by local
servers such as llama.cpp.

The same settings can behave differently across servers, models, versions, and
hardware. A fixed seed does not guarantee identical output everywhere.

If a request fails after enabling a sampler:

1. Disable the sampler.
2. Send the message again.
3. Check your LLM server's documentation for the exact parameter name and
   supported range.

Backend-specific JSON can be added under **Settings → API → Context & Generation
→ Extra request parameters**.

## Simple starting points

For general chat:

```text
Temperature: 0.8
Min-P: 0.05
Repetition penalty: 1.05
```

For more predictable replies, lower Temperature. For more varied replies, raise
it gradually. Avoid enabling several advanced filters at once unless you know
how your server combines them.
