# Using Cozy

This guide explains what you can do in a chat: send messages, get a different
reply, edit and delete messages, branch a story, and keep several chats with
one character.

It assumes Cozy is connected to an AI server and you have a character to talk
to. If not, do [Getting started](1-getting-started.md) first.

Go to the part you need:

- [The chat screen](#the-chat-screen)
- [Send a message](#send-a-message)
- [Get a different reply (swipes)](#get-a-different-reply-swipes)
- [Edit, copy or delete a message](#edit-copy-or-delete-a-message)
- [Branch a chat](#branch-a-chat)
- [Manage a character's chats](#manage-a-characters-chats)
- [Slash commands](#slash-commands)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [On a phone or tablet](#on-a-phone-or-tablet)

## The chat screen

The other sections use these names for the parts of the screen.

| Part | Where it is | What it is for |
|---|---|---|
| Sidebar | The left column. On a phone it is hidden until you swipe right. | Your characters. Click one to open your latest chat with it. |
| Persona card | The bottom of the sidebar, showing your persona's name. | Click it to switch persona or to edit one. |
| **Settings** button | Under the persona card. | Opens Settings. |
| Chat | The large area. | The messages. Yours are in a box on the right. The character's are plain text on the left. |
| Context meter | The thin colored bar above the chat box. | Shows how full the AI's memory is. See [The context meter](4-prompts-and-memory.md#the-context-meter). |
| Chat box | The bottom of the screen. | Where you type. |
| Chats button | Left end of the chat box, a speech bubble. | Opens the chats panel: every chat you have with this character. |
| Memory button | Next to the chats button, a brain. | Opens the memory panel: Author's Note, lorebook and Auto Summary for this chat. See [Prompts and memory](4-prompts-and-memory.md). |
| **Send** button | Right end of the chat box. | Sends your message. |

## Send a message

1. Type in the chat box.
2. On a computer, press **Enter**. On a phone or tablet, tap **Send**.

The reply appears a few words at a time.

To start a new line without sending, press **Shift+Enter** on a computer. On a
phone or tablet, the keyboard's Enter key always starts a new line.

**To stop a reply early:** while a reply is being written, the **Send** button
becomes a **Stop** button. Click it. Cozy keeps the part of the reply that had
already arrived.

**To get a reply without writing anything:** click **Send** while the chat box
is empty. The character writes the next message. Your side adds nothing.

**Replies finish without you.** Once a message is sent, the Cozy server writes
the reply and saves it to the chat. You do not need to keep the page open:

- You can lock your phone, switch to another chat, or close the page. The
  reply is in the chat when you come back. If it is still being written, you
  see it continue from where it has got to.
- A second device that opens the same chat sees the same reply arrive.
- In a browser, the tab shows the open character's name. A dot in front of the
  name means a reply finished while you were on another tab.
- Only **Stop** ends a reply early.
- If the reply failed while you were away, the chat says why when you come
  back, with a **Retry** button.
- One case loses a reply: Cozy itself is restarted while the reply is being
  written.

**If a reply fails:** an error appears with a **Retry** button. **Retry** asks
again for the same reply, so you do not need to send your message twice. See
[Connection test fails](7-troubleshooting.md#connection-test-fails) for what the
errors mean.

## Get a different reply (swipes)

A swipe is another version of the same reply. Each reply can have many swipes.
You choose which one is shown, and the one shown is the one the AI sees as the
story from then on.

Point at a reply to see its buttons, at the top right of the message. On a
phone the buttons are under the message.

- **To ask for a new version:** on the newest reply, click the right arrow
  (**→**). The new version is added as a new swipe, and the old ones are kept.
- **To move between versions:** click the left arrow and the right arrow. The
  counter between them, such as **2/3**, means you are on swipe 2 of 3.

Only the newest reply can get a new version. On an older reply you can still
move between the swipes it already has.

A character's first message works the same way, with one difference. Its
swipes are the card's alternate greetings, so you can move between them, but
you cannot generate a new one.

## Edit, copy or delete a message

Point at a message to see its buttons. On a phone they are under the message.
They work on your own messages and on the character's.

| Button | What it does |
|---|---|
| **Copy** | Copies the message's text. |
| **Edit** | Lets you change the text in place. Press **Enter** or click the check mark to save. Press **Esc** or click the **✕** to cancel. On a phone, use the two buttons. |
| **Fork** | Only on the character's replies. See [Branch a chat](#branch-a-chat). |
| **Delete** | Deletes this one message, after asking. If the message has swipes, all of them are deleted. Messages before and after it stay. Hold **Shift** while you click to skip the question. |

Editing a reply changes what the AI sees from then on. Deleting cannot be
undone.

## Branch a chat

A branch is a copy of a chat up to one reply, which you can then continue in a
different direction. The original chat does not change.

1. Point at the reply where the story should split.
2. Click **Fork**, the button with the branching arrows.

Cozy copies every message up to and including that reply into a new chat, and
opens the new chat.

Both chats now show a small pill under that reply, such as **2/2**. It means
"this is branch 2 of 2 at this reply".

- Click the pill to open the next branch. The reply stays in the same place on
  screen, so you can compare how each branch continued.
- Fork the same reply again to add a third branch.

A branch is an ordinary chat. It is in the chats panel, where the line under
its name says which chat it came from. Rename or delete it like any other
chat. If you delete the original chat, the pill disappears from its branches.

If the chat uses Auto Summary, the branch keeps its memory. See
[Branches](4-prompts-and-memory.md#branches) in Prompts and memory.

## Manage a character's chats

You can have any number of chats with one character. Click the chats button
(the speech bubble at the left of the chat box) to open the chats panel.

| To do this | Do this |
|---|---|
| Start a new chat | Click **+** at the top of the panel, or type `/new`. |
| Open another chat | Click its name in the list. The newest is at the top. |
| Rename a chat | Point at the chat, click **⋯**, then **Rename**. Or type `/rename` for the open chat. |
| Save a chat to a file | Click **⋯**, then **Export**. Or type `/export` for the open chat. You get a `.jsonl` file. |
| Load a chat from a file | Click the import button, next to **+**, and choose a `.jsonl` file. It is added as a new chat with this character. |
| Delete a chat | Click **⋯**, then **Delete**. This cannot be undone. |

A chat is named after the date and time it was started until you rename it.
Under each name, the list says how long ago the chat was last used.

Cozy remembers the chat you were last in with each character. Clicking a
character opens that chat, on any device.

## Slash commands

A slash command is a line you type in the chat box that starts with `/`. It
runs an action. It is not sent to the AI.

1. Type `/` in the chat box. A list of commands opens.
2. Keep typing to narrow the list, or use the **↑** and **↓** keys.
3. Press **Tab** to fill in the highlighted command without running it.
4. Press **Enter** to run it.

After a command that takes a name, type a space, and Cozy lists the names. The
start of a name is enough when only one name fits. For example, `/pe` **Tab**
`al` **Tab** leaves `/persona Alice`, ready for **Enter**.

| Command | What it does |
|---|---|
| `/retry` | Asks for a new version of the last reply. The same as **→** on its last swipe. |
| `/new` | Starts a new chat with this character. |
| `/rename` | Renames the open chat. |
| `/export` | Saves the open chat to a file. |
| `/jump` | Scrolls to the **Context window** line, which marks the oldest message the AI still sees. See [The context meter](4-prompts-and-memory.md#the-context-meter). |
| `/meter` | Hides or shows the context meter. |
| `/context <tokens>` | Sets **Max context tokens**. `/context 16k` means 16384. With no number, it shows the current value. |
| `/auto-summary on` or `off` | Turns Auto Summary on or off for the open chat. With neither word, it switches it. |
| `/prompt <name>` | Switches the prompt preset. If two versions have that name, it picks the newer one. `/prompt NanoBear 2.1` picks an exact version. |
| `/api <name>` | Switches the API preset. |
| `/persona <name>` | Switches your persona. |
| `/char <name>` | Opens your latest chat with that character. |
| `/settings` | Opens Settings. `/settings api` opens one page of it. |

`/prompt`, `/api`, `/persona` and `/char` typed with no name show which one is
active and which are available.

## Keyboard shortcuts

These work on a computer.

| Key | Where | What it does |
|---|---|---|
| **Enter** | Chat box | Sends the message. |
| **Shift+Enter** | Chat box | Starts a new line. |
| **↑** | Chat box, empty | Opens your last message for editing. |
| **←** and **→** | Chat box, empty | Move through the swipes of the newest reply. **→** on the last swipe asks for a new one. If your own message is the newest, **→** asks for a reply. |
| **/** | Anywhere outside a text box | Puts the cursor in the chat box. The slash is not typed. |
| **Enter** | While editing a message | Saves the edit. |
| **Esc** | While editing a message | Cancels the edit. |
| **Esc** | A panel, menu or Settings is open | Closes it. |
| **⌘F** or **Ctrl+F** | Anywhere | The browser's own Find. It searches the whole chat, not only the part on screen. |

## On a phone or tablet

Cozy works the same on a phone, with these differences:

- **The sidebar is hidden.** Tap **☰** at the top left to open it, or swipe
  right on the chat. Start the swipe a finger's width in from the left edge,
  because the very edge of the screen belongs to the phone's own Back gesture.
  Swipe the sidebar left to close it.
- **A message's buttons are under the message**, and are always shown.
- **The chats panel and the memory panel open from the bottom.** Pull one down
  by the handle at its top to close it.
- **Settings opens as a list.** Tap a section to open its page. Tap the back
  arrow to return to the list. **Back to chat** is at the bottom of the list.
- **The system's Back closes what is open.** That is the swipe from the screen
  edge on an iPhone, and the back gesture or button on Android. It closes the
  sidebar, Settings, a panel or a question Cozy is asking. It does not leave
  Cozy. Inside Settings it goes back one step at a time.
- **Enter starts a new line.** Tap **Send** to send.

To open Cozy on your phone in the first place, and to install it on the Home
Screen, see [Mobile use](../README.md#mobile-use) in the README.

---

**Next:** [Themes](3-user-themes.md) explains how to change the colors, and
how to make a theme of your own.

Back to the [list of all guides](../README.md#documentation).
