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
    --user-msg-bg: #384f78;

    --text-color: #edf1f7;
    --text-secondary: #9ca8ba;

    --accent-color: #8fb8ff;
    --accent-hover: #b2ceff;
    --border-color: #30394a;
}
```

The `@import` line loads Cozy's complete variable set. The declarations under
`:root` replace only the values you want to change.

The import must be the first rule in the file.

## Select the theme

1. Reload Cozy after creating the file.
2. Open **Settings → General**.
3. Select `my-theme` under **Theme**.

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
- Character gallery colors
- Roleplay Markdown colors
- User-message colors

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
