# Code Cleanup Backlog

> Remaining items from a full codebase audit. All Phase 1–3 items are resolved.

---

## 1. Inconsistent `request.get_json()` semantics

**Files**:
- `force=True`: `routes/messages.py:143`, `routes/settings.py:73,106,122,245,267`, `routes/llm.py:74`
- `silent=True`: `routes/characters.py:252`, `routes/chats.py:190,292`, `routes/personas.py:28,48`, `routes/lorebooks.py:183,210,324`, `routes/messages.py:74,117`

**Problem**: `force=True` ignores Content-Type header and raises 400 on unparseable JSON. `silent=True` silently returns `None` (coerced to `{}` by the `or {}` idiom). These have very different error-handling semantics — some callers silently accept empty bodies, others reject malformed ones.

**Plan**:
1. Decide on a project-wide standard. Recommendation: use `silent=True` everywhere and validate required fields explicitly at the top of each handler. This is the safer pattern — it never crashes on bad input and gives the route a chance to return a specific error message.
2. Replace all `force=True` calls with `silent=True`.
3. For routes that currently rely on `force=True` raising 400 on bad JSON (e.g. `settings.py`), add an explicit check: if the parsed result is `None` and the request body is non-empty, return `400 {'error': 'Invalid JSON'}`.
4. Add a test verifying that each JSON-accepting endpoint returns 400 for malformed JSON, not 500.

---

## 2. `update_character` allows arbitrary key injection

**File**: `routes/characters.py:262-263`

**Problem**: `for key in data: existing_data[key] = data[key]` blindly merges every user-supplied key into the card's `data` dict. A client could inject or overwrite keys like `spec`, `spec_version`, `extensions`, or arbitrary fields that shouldn't be user-controlled.

**Plan**:
1. Define an allowlist of updatable fields — use `CARD_DATA_DEFAULTS` keys from `card_store.py` as the canonical set: `name, description, personality, scenario, first_mes, mes_example, creator_notes, system_prompt, post_history_instructions, alternate_greetings, tags, creator, character_version`.
2. In `update_character()`, filter `data` to only allowlisted keys before merging:
   ```python
   ALLOWED_UPDATE_KEYS = set(CARD_DATA_DEFAULTS) - {'character_book', 'extensions'}
   for key in data:
       if key in ALLOWED_UPDATE_KEYS:
           existing_data[key] = data[key]
   ```
   (Exclude `character_book` because it has its own dedicated route; exclude `extensions` or handle it separately.)
3. Add a test that PUTs a character with an injected `spec_version` key and asserts it's ignored.

---

## 3. Inconsistent 404 error message formats

**Files**: All route files — 18 occurrences of `{'error': 'Not found'}`, no specific entity names.

**Problem**: Every 404 response uses the generic `"Not found"`. A client (or developer debugging) can't tell whether a character, chat, persona, lorebook, system prompt, or preset was missing. Other error messages in the same files are specific (e.g. `"No endpoint configured"`, `"name is required"`).

**Plan**:
1. Replace every `{'error': 'Not found'}` with a specific entity name:
   - `characters.py` → `"Character not found"`
   - `chats.py` → `"Chat not found"`
   - `personas.py` → `"Persona not found"`
   - `lorebooks.py` → `"Lorebook not found"`
   - `settings.py` system prompts → `"System prompt not found"`
   - `settings.py` presets → `"Preset not found"`
2. Update any existing tests that assert on the exact `"Not found"` string.
3. Consider extracting a shared `not_found(entity)` helper in a future pass if desired, but for now explicit strings are clearer.

---

## 4. Non-transactional `embed_in_character`

**File**: `routes/lorebooks.py:251-272`

**Problem**: `embed_in_character` opens two separate `get_db()` contexts. The first reads the lorebook, then `set_character_book(char_id, book)` writes the character card to disk (outside the DB context). If the second `get_db()` block (which deletes the standalone lorebook and clears FK references) fails, the character book has already been embedded on disk but the standalone lorebook row remains — an orphan.

**Plan**:
1. Restructure to use a single `get_db()` context that encompasses both the read and the deletion:
   ```python
   def embed_in_character(book_id, char_id):
       delete_standalone = request.args.get('delete_standalone') == '1'
       with get_db() as conn:
           row = conn.execute('SELECT * FROM lorebooks WHERE id=?', (book_id,)).fetchone()
           if not row:
               return jsonify({'error': 'Lorebook not found'}), 404
           book = _parse_book(row['book'])
           _, err = set_character_book(char_id, book)
           if err:
               return jsonify({'error': err}), 404
           if delete_standalone:
               conn.execute('UPDATE chats SET active_lorebook_id=NULL WHERE active_lorebook_id=?', (book_id,))
               conn.execute('DELETE FROM lorebooks WHERE id=?', (book_id,))
       return jsonify({'success': True, 'character_book': book})
   ```
2. The disk write (`set_character_book`) is non-transactional by nature (SQLite can't roll back file I/O), but at least the DB state will be consistent — if the write fails, we return early before deleting.
3. Add a test: embed a lorebook with `delete_standalone=1`, verify the standalone row is gone and the character has the book.

---

## 5. Two separate click handlers on `el.chatHistory`

**File**: `static/js/main.js:594-620` (avatar expand) and `:622-665` (message actions)

**Problem**: Two independent `addEventListener('click', ...)` on the same element. They don't conflict because each checks for different targets (`.avatar` vs `.message` / toolbar buttons), but merging them into one handler would be cleaner and avoids dispatching two listener calls per click.

**Plan**:
1. Merge both listeners into a single `el.chatHistory.addEventListener('click', ...)` handler.
2. Structure with clear early-returns:
   ```js
   el.chatHistory.addEventListener('click', async e => {
       // Avatar expand/collapse
       const avatar = e.target.closest('.message-container .avatar[data-has-image="true"]');
       if (avatar) { /* ... existing avatar logic ... */ return; }

       // Message toolbar actions
       let msgEl = e.target.closest('.message');
       if (!msgEl) { const wrapper = e.target.closest('.message-wrapper'); if (wrapper) msgEl = wrapper.querySelector('.message'); }
       if (!msgEl) return;
       // ... existing message action logic ...
   });
   ```
3. Delete the second `addEventListener` call entirely. No functional change.

---

## 6. Dynamic import for circular dependency in `sampler.js`

**File**: `static/js/sampler.js:156`

**Problem**: `import('./llm-settings.js').then(mod => mod.saveLLMSettings(...))` uses a dynamic import to avoid a circular dependency. If the module fails to load (network error, syntax error), the `.then` never fires and there's no `.catch` — the save is silently dropped.

**Plan**:
1. Add a `.catch` handler:
   ```js
   import('./llm-settings.js')
       .then(mod => mod.saveLLMSettings({ active_samplers: [...state.activeSamplers].join(',') }))
       .catch(err => console.error('Failed to save sampler settings:', err));
   ```
2. Alternatively, consider a lightweight event bus: `sampler.js` dispatches a custom event on `document`, and `llm-settings.js` listens for it. This eliminates the dynamic import entirely:
   ```js
   // sampler.js
   document.dispatchEvent(new CustomEvent('sampler-changed', { detail: { active_samplers: [...state.activeSamplers].join(',') } }));

   // llm-settings.js (during init)
   document.addEventListener('sampler-changed', e => saveLLMSettings(e.detail));
   ```
   The event bus approach is cleaner but touches two files. The `.catch` fix is minimal. Recommend `.catch` first, event bus as a follow-up if the circular dependency pattern appears elsewhere.

---

## 7. Modal delete handler duplicates `deleteCharacter`

**Files**: `static/js/modal.js:302-334` vs `static/js/characters.js:111-131`

**Problem**: The modal's delete button has its own inline character-deletion logic (API call, state cleanup, UI update, auto-select next character). This duplicates and diverges from `deleteCharacter()` in `characters.js` — e.g., the modal version doesn't reset `el.currentCharName.textContent` to `'Cozy'`, doesn't call `renderChats()`, and uses `el.chatHistory.innerHTML = ''` instead of `renderMessages()`. Over time these two paths will drift further.

**Plan**:
1. In `modal.js`, replace the inline delete logic with a call to the shared function:
   ```js
   import { deleteCharacter } from './characters.js';
   // ...
   deleteBtn.addEventListener('click', async () => {
       if (!editingCharId) return;
       close(); // close modal first so it doesn't block
       await deleteCharacter(editingCharId);
   });
   ```
2. Remove all the duplicated state-cleanup code from the modal handler (lines 308–333).
3. If `deleteCharacter` needs the custom confirm message with the character's name, add an optional `name` parameter to `deleteCharacter(charId, name)` that overrides the default confirm text.
4. Verify the modal's delete flow matches the sidebar delete flow end-to-end.

---

## 8. Missing test coverage

**Areas**:
- LLM streaming endpoint beyond basic content-type checks
- Prompt-builder/resolver logic (`resolveTemplateVariables`)
- `/api/llm/chat` prompt assembly route
- Settings whitelist / unknown key rejection
- Character update (PUT `/api/characters/:id`) basic fields
- Chat rename endpoint
- Persona avatar deletion flow
- Theme serving precedence (shadowing built-in with same filename)

**Plan** (each as a separate test module or class):

1. **LLM streaming**: Mock `requests.post` to yield SSE lines; verify the `/api/llm/chat` endpoint returns `text/event-stream`, forwards `data:` lines, and emits `data: [DONE]` at the end. Test error path (upstream 502) yields an error event.

2. **`resolveTemplateVariables`**: Unit-test in `static/js/` via a Node runner or by porting the function to Python for test purposes. Cover: `{{var}}` substitution, `{{#var}}...{{/var}}` conditional blocks, missing variables, nested conditionals.

3. **`/api/llm/chat` prompt assembly**: Post a valid payload with `model` and `messages`; verify the endpoint proxies to the configured URL and streams back. Test missing `model` returns 400. Test missing endpoint returns 400.

4. **Settings whitelist**: PUT `/api/settings` with a known key (e.g. `api_endpoint`) — should succeed. PUT with an unknown key (e.g. `admin_mode`) — should be ignored or rejected.

5. **Character update (PUT)**: After the key-injection fix (item 2 above), test that valid fields (`name`, `description`) are persisted and invalid keys (`spec`, `spec_version`) are ignored.

6. **Chat rename**: PUT `/api/characters/:id/chats/:id` with `{"name": "New Name"}` — verify the response and a subsequent GET both reflect the new name. Test empty name, XSS attempts.

7. **Persona avatar deletion**: Create a persona, upload an avatar, delete the persona — verify the avatar file is removed from disk. Delete a persona without an avatar — verify no error.

8. **Theme serving precedence**: Place a theme file in both `static/themes/` and `$DATA_DIR/themes/` with the same filename. GET `/api/themes` should list only one entry. GET `/themes/<name>.css` should serve the data-dir version.
