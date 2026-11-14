# User themes

A user theme is one CSS file that changes Cozy's colors. To add one:

1. Put a `.css` file in the `data/themes/` folder.
2. Reload the Cozy page in your browser.
3. Pick the theme under **Settings → General → Theme**.

A theme can change colors, shadows and how round the corners are. It cannot
change fonts, text size or layout. See
[What a theme cannot change](#what-a-theme-cannot-change).

The rest of this page gives the details:

- [Make your first theme](#make-your-first-theme)
- [File name rules](#file-name-rules)
- [Edit a theme](#edit-a-theme)
- [Start from a different built-in theme](#start-from-a-different-built-in-theme)
- [Replace a built-in theme](#replace-a-built-in-theme)
- [Variable reference](#variable-reference)
- [What a theme cannot change](#what-a-theme-cannot-change)
- [Install someone else's theme](#install-someone-elses-theme)
- [Fix a theme that does not work](#fix-a-theme-that-does-not-work)

## Make your first theme

This takes about two minutes. You do not need to stop or restart Cozy.

### 1. Find the themes folder

The folder is `data/themes/`, inside the folder where you installed Cozy. Cozy
creates it the first time it starts.

- If you set `COZY_DATA_DIR`, the folder is `themes/` inside that directory
  instead. See
  [Use a different data directory](2-advanced-setup.md#use-a-different-data-directory).
- Under Docker it is the same `data/themes/` folder on your computer. You do not
  need to copy anything into the container.

Do not put your theme in `static/themes/`. That folder holds Cozy's built-in
themes, and an update can overwrite it.

### 2. Create the file

Create a file named `my-theme.css` in the themes folder, with this content:

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
    --border-color: #30394a;
}
```

What each part does:

- The `@import` line loads Cozy's default theme, so every color starts with a
  value. It must be the first line of the file. CSS ignores an `@import` that
  comes after any other rule.
- The lines inside `:root { }` replace eight of those values with your own.
  Each line is one variable: a name that starts with `--`, a colon, a color,
  and a semicolon.

### 3. Reload Cozy

Reload the page in your browser. Cozy reads the themes folder each time the
page loads.

### 4. Pick the theme

1. Click **Settings** at the bottom left.
2. Click **General**.
3. Open the **Theme** list and choose **My Theme**.

The page changes right away, with no Save button. With the example above, the
gray background turns dark blue and the yellow accent turns light blue.

Some colors stay as they were, because the example does not set them: speech
in quotes stays green, the character's name stays pink, and the icons on
settings cards stay green. Each of those has its own variable. Find it in the
[Variable reference](#variable-reference) and add it to your file.

If **My Theme** is not in the list, see
[Fix a theme that does not work](#fix-a-theme-that-does-not-work).

## File name rules

Cozy lists a file as a theme only if all of these are true:

| Rule | Works | Does not work |
|---|---|---|
| The name ends in `.css`, in lowercase. | `my-theme.css` | `my-theme.CSS`, `my-theme.css.txt` |
| The name does not start with a period. | `my-theme.css` | `.my-theme.css` |
| The file is directly in the themes folder, not in a subfolder. | `data/themes/my-theme.css` | `data/themes/mine/my-theme.css` |

The name in the **Theme** list comes from the file name. Cozy removes `.css`,
turns each hyphen into a space, and capitalizes each word:

| File name | Name in the list |
|---|---|
| `my-theme.css` | My Theme |
| `night.css` | Night |
| `rose-gold-light.css` | Rose Gold Light |

Use lowercase letters, digits and hyphens in the file name. End the name of a
light theme in `-light`, such as `rose-gold-light.css`. If that file is later
deleted, Cozy then falls back to its light theme instead of its dark one.

## Edit a theme

1. Change the file and save it.
2. Reload the Cozy page.

A normal reload is enough. Cozy notices that the file changed and loads the new
version. You do not need to restart Cozy.

The theme you pick is saved in the browser, not on the server. So:

- Each browser and each device has its own choice. Picking a theme on your
  computer does not change it on your phone.
- If you delete or rename the file of the theme a browser is using, that
  browser shows the default **Cozy** theme the next time the page loads. It
  shows **Cozy Light** instead if the old file name ended in `-light`.

Themes are part of your data. **Download backup** includes them. See
[Data and backups](3-data-and-backups.md).

## Start from a different built-in theme

The `@import` line decides which built-in theme supplies the values you do not
set. Change the file name in it to start from another one:

```css
@import url("/themes/everforest-dark.css");
```

Cozy has six built-in themes:

| File name to import | Name in the list | Kind |
|---|---|---|
| `cozy.css` | Cozy (the default) | Dark |
| `cozy-light.css` | Cozy Light | Light |
| `everforest-dark.css` | Everforest Dark | Dark |
| `everforest-light.css` | Everforest Light | Light |
| `solarized-dark.css` | Solarized Dark | Dark |
| `solarized-light.css` | Solarized Light | Light |

**For a light theme, import a light built-in theme.** The light themes also
set softer shadows. A light theme that imports a dark one keeps the heavy dark
shadows unless you set `--card-shadow`, `--lift-shadow` and `--shadow-md`
yourself.

You can read the built-in files in
[`static/themes/`](../static/themes/). Read them, but do not edit them there.

You can also copy a whole built-in file into `data/themes/` under a new name
and edit the copy, with no `@import` line. Importing is the safer choice: if a
later version of Cozy adds a variable, a theme that imports gets a value for it
and a copied file does not.

## Replace a built-in theme

A file in `data/themes/` with the same name as a built-in theme replaces that
built-in theme. For example, `data/themes/cozy.css` replaces the default
**Cozy** theme for every browser that uses it.

Two rules apply:

- A replacement cannot import the file it replaces. `@import
  url("/themes/cozy.css")` inside `data/themes/cozy.css` would import itself.
  Either import a different built-in theme, or write out every required
  variable from the [Variable reference](#variable-reference).
- Another theme that imports `/themes/cozy.css` now gets your replacement, not
  the built-in file.

To undo it, delete your file. The built-in theme comes back on the next reload.

Use a file name of your own unless you want this.

## Variable reference

A theme sets CSS variables. Cozy's own stylesheet reads them. This section
lists every variable a theme may set.

- **Required** variables have no fallback. Every built-in theme sets all of
  them, so a theme that starts with an `@import` line already has them. A theme
  with no `@import` line must set every one.
- **Optional** variables have a fallback, shown in the tables. Leave one out
  and Cozy uses the fallback.

[`static/themes/cozy.css`](../static/themes/cozy.css) is a complete working
example with comments.

### Required: surfaces and lines

| Variable | What it colors |
|---|---|
| `--bg-color` | The main surface: the page, the chat and the Settings pages. |
| `--app-bg` | The darkest ground: the loading screen, and quote blocks in your own messages. |
| `--sidebar-bg` | The sidebar. |
| `--char-msg-bg` | Raised surfaces: settings cards and your own message bubble. |
| `--input-bg` | Text fields and other sunken areas. |
| `--hover-bg` | The fill under the pointer on buttons and rows. |
| `--active-bg` | The selected row, such as the open character. |
| `--border-color` | Borders, divider lines and scrollbars. |
| `--shadow-md` | The shadow under things that float: menus, panels, dialogs and notices. A `box-shadow` value, not a color. |

### Required: text, accent and status

| Variable | What it colors |
|---|---|
| `--text-color` | Normal text. |
| `--text-secondary` | Quieter text: hints, timestamps, placeholders. |
| `--accent-color` | The accent: the Send button, sliders, switches, and (unless you set `--accent-text`) labels, active rows and focus rings. |
| `--success-color` | "Saved" and "Copied" marks, and the fallback avatar of a persona. |
| `--danger-color` | Delete buttons, the Stop button, and the context meter when the context is nearly full. |
| `--danger-bg-hover` | The fill behind **Delete** in a character's menu while the pointer is on it. |
| `--accent-edit-bg`, `--editable-bg` | The tint behind a message's text while you edit it. |

### Required: message text

Cozy colors the text of a message by its Markdown formatting.

| Variable | What it colors |
|---|---|
| `--rp-dialogue-color` | Speech in quotation marks. |
| `--rp-action-color` | `*Italic*` text, usually actions and thoughts. |
| `--rp-emphasis-color` | `**Bold**` text. Also used for warnings. |
| `--rp-intense-color` | `***Bold italic***` text. |
| `--rp-quote-color`, `--rp-quote-bg` | The text and the background of a `>` quote block. |
| `--rp-ooc-color` | The text of `` `inline code` ``, and the line through struck-out text. |
| `--code-bg`, `--code-border` | The background and border of inline code. |
| `--pre-bg`, `--pre-border` | The background and border of a code block. The prompt preview uses the background too. |

### Optional: general

The built-in themes set some of these. A theme that imports one keeps the
imported value until it sets its own.

| Variable | What it does | Fallback |
|---|---|---|
| `--radius-md` | How round the corners of cards are. Buttons and fields take 0.6 of it. `0px` makes cards, buttons and fields square. | `8px` |
| `--accent-text` | The accent where it is used as text: labels, active rows, focus rings. Set a darker or lighter step of the accent when the accent itself is hard to read on your surfaces. | `--accent-color` |
| `--accent-alt` | A second accent, for the icons beside settings card titles. | `--success-color` |
| `--cool-color` | Links, references and previews. | A mix of `--rp-action-color` and `--text-color` |
| `--char-name-color` | The character's name above its messages. | The accent text color |
| `--btn-primary-color` | Text and icons on accent-filled buttons, such as Send. | `white` |
| `--btn-danger-hover-color` | Text on a danger button while the pointer is on it. | `white` |
| `--avatar-text-color` | The initials on an avatar that has no picture. | `white` |
| `--overlay-bg`, `--overlay-color` | The label that appears over an avatar in an editor when you point at it. | Half-transparent black, `white` |
| `--backdrop-bg` | The dim layer behind a dialog, and behind the sidebar on a phone. | `rgba(0, 0, 0, 0.35)` |
| `--card-shadow` | The shadow under a card. | A shadow made for dark themes |
| `--lift-shadow` | The shadow under a card or button while the pointer is on it. | A shadow made for dark themes |
| `--input-shadow` | A shadow on text fields. | `none` |
| `--rp-intense-shadow` | A glow on `***bold italic***` text. | A faint red glow |
| `--rp-dialogue-weight` | How bold speech in quotation marks is. | `500` |
| `--rp-dialogue-shadow` | A glow on speech in quotation marks. | `none` |
| `--rp-strike-opacity` | How faded struck-out text is, from `0` to `1`. | `0.5` |

### Optional: the context meter

The context meter is the thin bar above the chat box. Each segment has its own
color, and the segments have to stay easy to tell apart.

| Variable | Segment | Fallback |
|---|---|---|
| `--meter-system-prompt` | Your prompt | `#8b5cf6` |
| `--meter-character-card` | The character | `#ec4899` |
| `--meter-persona` | Your persona | `#06b6d4` |
| `--meter-lorebook` | Lorebook entries | `#22c55e` |
| `--meter-author-note` | The Author's Note | `#eab308` |
| `--meter-auto-summary` | The summary | `#6366f1` |
| `--meter-message-history` | The chat messages | `#3b82f6` |
| `--meter-current-draft` | The message you are typing | `#14b8a6` |
| `--meter-response-reserve` | The room kept for the reply | `#f97316` |
| `--meter-unused` | The empty part | A mix of `--border-color` and `--bg-color` |

Only `cozy.css` and `cozy-light.css` set these. The other four built-in themes
use the fallbacks, which were chosen for dark themes. Set your own if they are
hard to see on your colors.

### Optional: your own messages

Your own message is drawn as a bubble. By default its text uses the same colors
as the character's text. The `--user-*` variables change the bubble alone. Set
only the ones you want to be different.

| Variable | What it colors in your bubble | Fallback |
|---|---|---|
| `--user-msg-bg` | The bubble. | `--char-msg-bg` |
| `--user-msg-color` | Normal text. | `--text-color` |
| `--user-msg-border` | The border. | `transparent` |
| `--user-msg-name-color` | Your persona's name. | `--text-color` |
| `--user-dialogue-color` | Speech in quotation marks. | `--rp-dialogue-color` |
| `--user-dialogue-weight` | How bold that speech is. | `--rp-dialogue-weight` |
| `--user-dialogue-shadow` | A glow on that speech. | `--rp-dialogue-shadow` |
| `--user-em-color` | `*Italic*` text. | `--rp-action-color` |
| `--user-em-shadow` | A glow on italic text. | `none` |
| `--user-strong-color` | `**Bold**` text. | `--rp-emphasis-color` |
| `--user-intense-color` | `***Bold italic***` text. | `--rp-intense-color` |
| `--user-intense-shadow` | A glow on bold italic text. | `none` |
| `--user-strike-color` | The line through struck-out text. | `--rp-action-color` |
| `--user-strike-opacity` | How faded struck-out text is. | `0.55` |
| `--user-heading-color` | Headings. | `--text-color` |
| `--user-h1-border` | The line under a top-level heading. | `--border-color` |
| `--user-hr-mid-color` | A horizontal rule. | `--border-color` |
| `--user-blockquote-color` | The text of a quote block. | `--text-color` |
| `--user-blockquote-bg` | The background of a quote block. | `--app-bg` |
| `--user-blockquote-border` | The bar beside a quote block. | `--border-color` |
| `--user-code-color` | The text of inline code. | `--rp-ooc-color` |
| `--user-code-bg` | The background of inline code. | `--code-bg` |
| `--user-code-border` | The border of inline code. | `--code-border` |
| `--user-pre-bg` | The background of a code block. | `--pre-bg` |
| `--user-pre-border` | The border of a code block. | `--pre-border` |
| `--user-editable-bg` | The tint behind the text while you edit it. | A faint tint of `--accent-color` |
| `--user-action-color` | The small buttons on the bubble (copy, edit, delete). | `--text-secondary` |
| `--user-action-hover-color` | Those buttons while the pointer is on one. | `--text-color` |
| `--user-action-hover-bg` | The fill behind a button while the pointer is on it. | `--hover-bg` |

[`everforest-dark.css`](../static/themes/everforest-dark.css) sets most of
these and is a good example to copy from.

## What a theme cannot change

**Fonts, text size and spacing.** Cozy sets these in its own stylesheet, which
the browser loads after your theme. A line such as `--font-sans: Georgia;` in
your `:root { }` block is read and then replaced, so nothing changes. The
same goes for every other name that Cozy's stylesheet defines itself, such as
`--font-mono` and `--text-md`. Only the variables in the
[Variable reference](#variable-reference) are meant for themes.

**Individual parts of the page.** A theme file is ordinary CSS, so the browser
accepts rules such as `#sidebar { ... }`. Cozy does not support them. The names
of its parts change between versions without notice, so a rule that works today
can stop working after an update. Where your rule and Cozy's own rule are
equally specific, Cozy's wins, because its stylesheet loads last. Variables
from the reference keep working.

## Install someone else's theme

1. Open the `.css` file in a text editor and read it before you use it.
2. Put it in `data/themes/`, then follow
   [Make your first theme](#make-your-first-theme) from step 3.

Only install themes from people you trust. A theme is CSS that runs in your
browser on the Cozy page. It can load fonts and images from other servers,
which tells those servers when you open Cozy. It can also hide or cover parts
of the page. Lines that contain `url(` with an address starting `http` are the
ones to look at.

If you use a [password](7-security.md#require-a-password): theme files are sent
to a browser before it signs in, so that the sign-in page can use your theme.
Do not write anything private in a theme file.

## Fix a theme that does not work

### The theme is not in the Theme list

Check each of these, in order:

1. The file is in the right folder. It must be `data/themes/`, or `themes/`
   inside your `COZY_DATA_DIR`. **Settings → About → Storage → Custom themes**
   shows how many files are in that folder. If it says 0 files, your file is
   somewhere else.
2. The file name follows the [File name rules](#file-name-rules). On Windows,
   turn on **File name extensions** in File Explorer to make sure the name does
   not really end in `.css.txt`.
3. You reloaded the page after you created the file.
4. Under Docker on Linux, you were able to save the file. Cozy's container
   owns `data/`, so your own account may not be allowed to write there. See
   [File permissions on Linux](2-advanced-setup.md#file-permissions-on-linux).

### The theme is in the list, but the page looks wrong

- **Most of the page lost its colors.** Your file has no `@import` line, or the
  line is not first, or the file name inside it is misspelled. Without it,
  every variable you did not set has no value.
- **Your changes do nothing at all.** Look for a missing `}` or a missing
  semicolon. The browser skips a line it cannot read, and a missing `}` makes
  it skip everything after it.
- **One color did not change.** That part of the page reads a different
  variable from the one you set. Find the right one in the
  [Variable reference](#variable-reference).
- **The font did not change.** A theme cannot change fonts. See
  [What a theme cannot change](#what-a-theme-cannot-change).

### You edited the file, but nothing changed

1. Check that you saved the file.
2. Reload the page.
3. Check under **Settings → General → Theme** that this browser is using the
   theme you edited.

For anything else, see [Troubleshooting](4-troubleshooting.md).

---

Back to the [list of all guides](../README.md#documentation).
