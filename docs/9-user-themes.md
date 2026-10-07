# User Themes

User themes are CSS files stored in:

```text
data/themes/
```

Python and Docker use the same host directory. Do not put user themes in
`static/themes/`; that directory belongs to the application and may change
during updates.

## Create a theme

Create `data/themes/my-theme.css`:

```css
@import url("/themes/cozy.css");

:root {
    --app-bg: #10131a;
    --bg-color: #181d27;
    --sidebar-bg: #141923;
    --char-msg-bg: #202736;

    --text-color: #edf1f7;
    --text-secondary: #9ca8ba;

    --accent-color: #8fb8ff;
    --accent-hover: #b2ceff;
    --border-color: #30394a;
}
```

The `@import` line loads Cozy's complete variable set. The declarations under
`:root` replace only the values you want to change. Most of the rest of
`cozy.css` (message text, code blocks, the user's message) is written in terms
of these, so it follows your colours.

The import must be the first rule in the file.

## Select the theme

1. Reload Cozy after creating the file.
2. Open **Settings → General**.
3. Select **My Theme** under **Theme**. The picker shows each filename in
   words, so `my-theme.css` reads as My Theme.

The selection is saved in the current browser. Other browsers and devices keep
their own selection.

Reload the page after editing the active theme. Use a hard refresh if the
browser still shows the old colors.

## Start from a built-in theme

Change the import to inherit a different built-in theme:

```css
@import url("/themes/everforest-dark.css");
```

Built-in theme files are listed in `static/themes/`.

You can also copy a complete built-in file into `data/themes/` and edit it.
Importing is usually easier because new theme variables added by Cozy remain
available automatically.

## Override a built-in theme

A user theme with the same filename as a built-in theme takes priority.

For example:

```text
data/themes/cozy.css
```

replaces the built-in `cozy.css`. Do not import `/themes/cozy.css` from that
file because it would import itself.

Use a unique filename unless you intentionally want this behavior.

## Theme variables

The complete current variable list is in
[`static/themes/cozy.css`](../static/themes/cozy.css). Common groups include:

- Page, sidebar, input, and message backgrounds
- Primary and secondary text
- Accent, success, and danger colors
- Borders, shadows, and corner radius
- Roleplay Markdown colors
- User-message colors
- Context token meter segments (`--meter-system-prompt`, `--meter-character-card`,
  `--meter-persona`, `--meter-lorebook`, `--meter-author-note`,
  `--meter-auto-summary`, `--meter-message-history`, `--meter-current-draft`,
  `--meter-response-reserve`, `--meter-unused`)

The meter needs nine colors that stay distinguishable from each other, more
than a palette usually carries. The two signature themes, `cozy` and
`cozy-light`, declare their own sets; every other built-in theme falls back to a
shared default set tuned for a dark palette. Declare them to retune the meter
for a lighter or quieter palette.

A few more variables are optional hooks. Of these, `cozy.css` sets only
`--cool-color` and `--accent-alt`, so a theme importing it keeps Cozy's blue and
green until it sets its own. Declare the others when your palette needs them:

- `--accent-text`: the accent as used for text (section labels, active rows,
  focus rings). Set it to a deeper or lighter step of the accent when the
  accent itself is too pale or too dark to read on your surfaces.
- `--cool-color`: the hue for links, references and code. Without it Cozy
  derives one from `--rp-action-color`.
- `--accent-alt`: a second accent for the icons beside settings card titles.
  Without it they take `--success-color`.
- `--shell-shadow`, `--card-shadow`, `--lift-shadow`: the shadows under the
  app's outer frame, under cards, and under a lifted button. The defaults suit
  a dark palette; light themes soften them.
- `--user-focus-color`: the focus ring inside a user message, for themes whose
  user bubble is filled with the accent.

Theme files are intended to override CSS variables. Direct component selectors
may break when Cozy's interface changes.

## Installing someone else's theme

CSS files can load remote fonts, images, and other resources. Only install
themes from sources you trust. Open the file in a text editor first if you are
unsure.

## Theme does not appear

Check these items:

1. The file is directly inside `data/themes/`.
2. The filename ends in `.css`.
3. The filename does not begin with a period.
4. The browser was reloaded after the file was created.
5. The CSS contains matching braces and valid variable declarations.
