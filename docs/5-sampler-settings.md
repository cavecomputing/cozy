# Sampler settings

Samplers are number settings that control how the AI chooses its next word.
Cozy's defaults suit most models: **Temperature 1.0**, **Min-P 0.05**, and
every other sampler off. If nobody has told you to use other values, change
nothing.

To change how replies read, start with one of these:

| You want | Do this |
|---|---|
| More predictable replies | Lower **Temperature**. |
| More varied replies | Raise **Temperature**, a little at a time. |
| Fewer repeated phrases | Turn on **Repetition penalty** and set it to about `1.05`. |

Change one setting at a time, so you can tell what each change did.

The samplers are in **Settings → API**, in the **Samplers** card. To turn a
sampler on or off, click the gear icon at the top right of that card. The
steps are in [Check the samplers](1-getting-started.md#2-check-the-samplers).

The rest of this guide:

- [How samplers are sent](#how-samplers-are-sent)
- [Core samplers](#core-samplers)
- [Advanced samplers](#advanced-samplers)
- [Which servers accept which sampler](#which-servers-accept-which-sampler)
- [Send a setting that is not in the list](#send-a-setting-that-is-not-in-the-list)

## How samplers are sent

- Cozy sends only the samplers that are turned on. A sampler that is off is
  not sent at all, and the server uses its own default for it.
- The AI server decides what to do with each value. A server that does not
  know a setting either ignores it or refuses the whole request.
- **Anthropic's Claude API gets no samplers.** Current Claude models choose
  their own sampling. While a Claude address is set, the **Samplers** card is
  faded and says so. **Max response tokens** is still sent. See
  [If you use Anthropic's Claude API](1-getting-started.md#if-you-use-anthropics-claude-api).

## Core samplers

**Temperature** and **Min-P** are on in a fresh install. Turn the others on
under **Active samplers** (the gear icon).

Each sampler that is on has a slider and a number box. They show the same
value. Drag the slider or type the number.

| Setting | Default | On in a fresh install | What it changes |
|---|---:|:---:|---|
| Temperature | 1.0 | yes | Higher values add variation. Lower values are more predictable. |
| Dynamic temperature (**Dynamic Temp** in the list) | Range 0, Exponent 1.0 | no | Varies the temperature while the reply is written. A range of `0` turns it off. |
| Top-P | 0.95 | no | Keeps only the most likely words, up to this total probability. `1.0` turns the filter off on most servers. |
| Min-P | 0.05 | yes | Removes words that are much less likely than the most likely word. `0` usually turns it off. |
| Top-K | 0 | no | Keeps only the K most likely words. `0` usually turns it off. |
| Repetition penalty (**Rep. Penalty** in the list) | 1.0 | no | Makes words that were already used less likely. `1.0` turns it off. |
| Last N tokens | 64 | no | How far back the repetition penalty looks. It appears when **Repetition penalty** is on. |

**Max response tokens** (512 by default) is not a sampler. It is the longest
reply the server may write, and it is under **Context & generation** on the
same page.

"Word" here means a token, which is a word or a piece of a word.

## Advanced samplers

These are off by default. Turn one on under **Active samplers** (the gear
icon). Setting a sampler to the value in the middle column has the same
effect as turning it off.

| Setting | Value that turns it off | What it does |
|---|---:|---|
| Top-N Sigma | `-1` | Removes low-scoring words, measured by a sigma threshold. |
| Presence penalty | `0` | Makes a word less likely once it has appeared at all. |
| Frequency penalty | `0` | Makes a word less likely the more often it has appeared. |
| DRY | Multiplier `0` | Makes repeating a whole sequence of words less likely. |
| XTC | Probability `0` | Sometimes removes the most likely words, for less predictable text. |
| Seed | `-1` | A fixed number makes the server start from the same random state each time. `-1` asks for a random one. |

Do not turn on several of these at once unless you know how your server
combines them.

## Which servers accept which sampler

- **Accepted by almost every server:** Temperature, Top-P, Presence penalty,
  Frequency penalty and Max response tokens.
- **Accepted mainly by servers on your own computer**, such as llama.cpp:
  all the others.

The same value can behave differently on another server, model or computer.
A fixed **Seed** does not give the same reply everywhere.

If a message fails after you turned a sampler on:

1. Turn that sampler off.
2. Send the message again.
3. Look in your AI server's documentation for the exact name of the setting
   and the values it accepts.

## Send a setting that is not in the list

Some servers accept settings that Cozy has no control for, such as Mirostat
or Typical-P. You can send them as JSON.

1. Open **Settings → General** and turn on **Show advanced configuration**.
2. Open **Settings → API**. An **Extra request parameters** card is now on
   the page.
3. Type the settings as JSON. For example `{"mirostat": 2, "mirostat_tau": 5.0}`
   or `{"typical_p": 0.9}`.

Cozy adds this JSON to every request. Use the setting names from your AI
server's documentation.

---

**Next:** [Running Cozy](6-running-cozy.md) explains Docker, other devices,
passwords, backups and updating.

Back to the [list of all guides](../README.md#documentation).
