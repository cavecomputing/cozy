# Code Cleanup Backlog

> Generated from a full codebase audit. Items are organized by priority.
> Tackled so far:
> - Docker docs port mismatch (`docs/run.md`) — fixed
> - Inconsistent DELETE/PUT success response keys — standardized to `{'success': True}`
> - Missing 404 check in `update_message` (`routes/messages.py`) — fixed

---

## High Priority

### Inconsistent `request.get_json()` semantics
- **Files**: `routes/messages.py`, `routes/settings.py`, `routes/llm.py` use `force=True`; `routes/characters.py`, `routes/chats.py`, `routes/personas.py`, `routes/lorebooks.py` use `silent=True`
- **Issue**: `force=True` ignores Content-Type and raises 400 on bad JSON. `silent=True` silently returns `{}`. These have different error-handling semantics and should be unified.

### [DONE] Duplicate "read character card data" extraction pattern
- **Locations** (6+):
  - `routes/characters.py:230`, `routes/characters.py:265`
  - `routes/chats.py:37`, `routes/chats.py:61`
  - `routes/lorebooks.py:286`, `routes/lorebooks.py:377`
- **Pattern**: `card = read_character_card(os.path.join(shared.CHARACTERS_DIR, row['filename'])); data = card.get('data', card) if card else {}`
- **Fix**: Extract a shared helper in `card_store.py` like `get_character_card_data(conn, char_id)`.

### [DONE] Missing 404 existence check in `delete_system_prompt`
- **File**: `routes/settings.py:140`
- **Issue**: Deletes without first checking the row exists. Every other DELETE endpoint verifies first. Risks silently succeeding on a non-existent resource.
- **Fix**: Added `SELECT` existence check before delete; returns 404 if missing. Same fix applied to `delete_preset` (same file, same bug).

---

## Medium Priority

### [DONE] Duplicate icons in `static/js/state.js`
- **Issue**: `icons.TRASH` and `icons.DELETE` are identical SVGs. `icons.EDIT` and `icons.PENCIL` differ only in size (14 vs 13).
- **Fix**: Consolidate into one icon each; control size via CSS.

### [DONE] Duplicate download-trigger pattern in JS
- **Files**: `static/js/api.js:56-66` (`exportCard`), `static/js/export.js:9-14` (`exportChat`)
- **Pattern**: Create `<a>`, set `href`/`download`, append, click, remove.
- **Fix**: Extract to `utils.js` as `downloadUrl(url, filename)`.

### [DONE] Duplicate filename sanitization regex
- **Files**: `static/js/api.js:58`, `static/js/export.js:11`
- **Pattern**: `/[\\/:*?"<>|]/g`
- **Fix**: Extract to `utils.js` as `sanitizeFilename(name)`.

### [DONE] Duplicate avatar-setup logic
- **Files**: `static/js/utils.js` (`applyAvatar`), `static/js/messages.js` (`buildMessageEl`), `static/js/personas.js` (`renderPersonaList`, `updateUserProfile`)
- **Issue**: Manual reimplementation of `backgroundImage`, `dataset.hasImage`, `textContent` pattern.
- **Fix**: Reuse `applyAvatar` or a shared helper.

### [DONE] Dead DOM reference in `static/js/state.js`
- **Line**: 184
- **Issue**: `el.lorebookConvert` targets `#settings-lorebook-convert` but no such element exists in `templates/index.html`.

### [DONE] Unused `icons.UPLOAD` in `static/js/state.js`
- **Line**: 204
- **Issue**: Defined but never referenced by any module. (Already absent from current file.)

### [DONE] Unused import in `static/js/send.js`
- **Line**: 2
- **Issue**: `maybeScrollToBottom` is imported from `utils.js` but never called. (Already used at line 47 in current file.)

### Fragile message deletion in `static/js/main.js:640-654`
- **Issue**: Matches messages by `rawText` content instead of `msgId`. Can fail with duplicate text. Should use `msgId`-based lookup (see `findStateMsg` in `messages.js`).
- **Phase**: 2

### [DONE] Dead condition in `static/js/messages.js:379`
- **Code**: `if (!char || !state.activeChat)`
- **Issue**: After two prior `!char` guards that return, `!char` is unreachable. Simplifies to `if (!state.activeChat)`.
- **Fix**: Removed unreachable `!char` from condition.

### [DONE] Variable shadowing in `static/js/system-prompts.js:91`
- **Code**: `const p = state.systemPrompts.find(p => ...)`
- **Issue**: Inner `p` shadows outer `const p`. Rename inner to `sp`.
- **Fix**: Renamed inner parameter to `sp`.

### `enforceAlternation` recreated every call in `static/js/request-builder.js:125-159`
- **Issue**: Defined inside `buildChatPayload()`, so a new closure is created on every invocation. Has no dependency on local variables — should be hoisted to module scope.
- **Phase**: 2

### Signal not passed to non-streaming path in `static/js/request-builder.js:178`
- **Code**: `return API.chatCompletion(payload)` ignores the `signal` parameter.
- **Issue**: If `generateResponse` is called with a signal but no `onToken`, the abort signal is silently dropped. `API.chatCompletion` doesn't accept a signal parameter.

### [DONE] `API.chatCompletion()` in `static/js/api.js` never called at runtime
- **Lines**: 142-152
- **Issue**: Only reachable via a fallback path in `request-builder.js` that's never exercised. Add a comment if kept intentionally.
- **Fix**: Added comment noting it's an unexercised fallback path kept as safety net.

### [DONE] Duplicate imports (same module, split statements)
- **Files**: `static/js/characters.js` (lines 3, 6), `static/js/chats.js` (lines 3, 5), `static/js/personas.js` (lines 3, 4), `static/js/send.js` (lines 4, 5)
- **Fix**: Consolidated into a single import statement per module.

### [DONE] Settings `mask_secret()` edge case
- **File**: `routes/settings.py:38`
- **Issue**: For strings 4–8 chars long, `value[:3] + '…' + value[-4:]` produces an overlapping mask (e.g. `"abc…bcde"` for `"abcde"`).
- **Fix**: Changed short-value mask to `'•••••'` to avoid overlap; the `> 8` threshold for partial masking remains.

### `persona_avatar_url` cross-module import
- **File**: `routes/messages.py:6`
- **Issue**: `from routes.personas import persona_avatar_url` creates coupling between route modules. Should live in a shared module.

### Duplicate avatar file-type validation
- **Files**: `routes/personas.py:97-99`, `routes/characters.py:310-312`
- **Pattern**: `ext = (file.filename or '').rsplit('.', 1)[-1].lower(); if ext not in shared.ALLOWED_IMG`
- **Fix**: Extract to a shared helper.
- **Phase**: 2

### `from pathlib import Path` in `routes/chats.py` (line 6)
- **Issue**: Used only once (line 251: `Path(upload.filename or '').stem`). `os.path.splitext` could do the same with the already-imported `os` module.
- **Phase**: 2

### `_iso_from_sqlite()` in `routes/chats.py:45-51`
- **Issue**: `fromisoformat`→`replace(tzinfo=timezone.utc)`→`isoformat()` round-trip is essentially a no-op plus timezone annotation for SQLite `CURRENT_TIMESTAMP` values. Somewhat misleading name.
- **Phase**: 2

### `_character_has_lorebook` and `_read_character_name` duplication in `routes/chats.py`
- **Lines**: 26-42 and 54-62
- **Issue**: Both query the `characters` table by `char_id` and read the character card from disk. Share the same preamble.
- **Fix**: Consolidate into a shared `_get_character_card(conn, char_id)` helper.

### `update_character` allows arbitrary key injection
- **File**: `routes/characters.py:268-269`
- **Issue**: `for key in data` blindly merges user-supplied keys into `existing_data` without validation or allowlist. A client could inject arbitrary keys (e.g. `spec`, `spec_version`).

### Inconsistent 404 error message formats
- **Issue**: Some endpoints use generic `"Not found"`; others use specific entity names (`"Character not found"`, `"Chat not found"`, etc.). Should be consistent.

### [DONE] `bool()` wrapper redundancy in `routes/chats.py:273`
- **Code**: `role = 'user' if bool(message.get('is_user')) else 'character'`
- **Issue**: `bool()` wrapper is redundant; the ternary already handles truthiness.
- **Fix**: Removed redundant `bool()` wrapper.

### Inconsistent persona update stripping
- **File**: `routes/personas.py:54-69`
- **Issue**: `name` is stripped, but `tagline` and `description` are not. In `create_persona` (lines 44-45), both are stripped. Should be consistent.
- **Phase**: 2

### [DONE] `_make_test_png()` wrapper in `tests/conftest.py:48-50`
- **Issue**: Trivial one-liner wrapper around `make_minimal_png()` that adds no value. Use `make_minimal_png()` directly.
- **Fix**: Removed wrapper; call `make_minimal_png()` directly.

### `_v2_card()` helper only in `tests/test_characters.py`
- **Issue**: Useful for building test data. Could benefit `test_lorebooks.py` which constructs card-like dicts manually.
- **Fix**: Move to `conftest.py` or a shared helper module.
- **Phase**: 3

### [DONE] Inconsistent inline imports in tests
- **Files**: `tests/test_lorebooks.py` (lines 504, 610), `tests/test_routes.py` (line 162)
- **Issue**: Some test methods have inline imports while other files keep all imports at the module top.
- **Fix**: Moved `from io import BytesIO` and `from png_utils import …` to module top; replaced `io.BytesIO` with `BytesIO` in `test_routes.py`.

### Missing test coverage areas
- LLM streaming endpoint beyond basic content-type checks
- Prompt-builder/resolver logic (`resolveTemplateVariables`)
- `/api/llm/chat` prompt assembly route
- Settings whitelist / unknown key rejection
- Character update (PUT `/api/characters/:id`) basic fields
- Chat rename endpoint
- Persona avatar deletion flow
- Theme serving precedence (shadowing built-in with same filename)

### Production deps in `requirements.txt`
- **Issue**: `livereload` and `pytest` are listed as production dependencies.
- `livereload` is not used at runtime (app.py explicitly avoids it because it buffers SSE).
- `pytest` should be a dev dependency.
- **Fix**: Move to a separate dev-requirements file or `pyproject.toml` dev group.
- **Phase**: 3

### Docker `COZY_DATA_DIR` set in both Dockerfile and docker-compose.yml
- **Issue**: Redundant. Compose value takes precedence, but duplication is confusing.
- **Phase**: 3

### Docker entrypoint missing `mkdir -p` for data subdirectories
- **Issue**: If a host volume mount overlays the Dockerfile's pre-created dirs, they might not exist. Consider adding `mkdir -p /data/characters /data/personas /data/themes`.
- **Phase**: 3

### CDN deps without SRI
- **File**: `templates/index.html` (lines 1043-1044)
- **Issue**: `marked.min.js` and `purify.min.js` loaded from CDN without `integrity` attributes or local fallbacks.
- **Phase**: 3

---

## Low Priority

### Two separate click handlers on `el.chatHistory` in `static/js/main.js`
- **Lines**: 594 and 622
- **Issue**: Both add click listeners on the same element. Could be consolidated into one handler with clear branching.

### [DONE] Redundant `renderChats()` call in `static/js/chats.js:106`
- **Issue**: Called after clearing state, then `loadChats()` calls it again after fetching. The first call renders an empty list. Harmless but slightly wasteful.
- **Fix**: Removed redundant `renderChats()` call in `characters.js` that preceded `loadChats()`.

### [DONE] Context-meter scrolls redundantly in `static/js/context-meter.js:37-41`
- **Issue**: Duplicates scroll-to-bottom behavior in `utils.js` `scrollToBottom()`. Could call the utility instead.
- **Fix**: Removed inline `requestAnimationFrame` scroll; `scrollToBottom()` in `utils.js` handles it.

### [DONE] Unicode escapes undocumented in `static/js/main.js:289-291`
- **Code**: `if (v && !v.startsWith('\u2022\u2022') && !v.includes('\u2026'))`
- **Issue**: Checks for masked password display (`\u2022` = bullet, `\u2026` = ellipsis). Not self-documenting. Add a comment or named constant.
- **Fix**: Added inline comment explaining the masked API key checks.

### [DONE] `estimateTextTokens` dead export in `static/js/tokenizer.js`
- **Line**: 18
- **Issue**: Exported but never imported by any other module. Could be made non-exported (private) since no other module calls it.
- **Fix**: Removed `export` keyword; function is now module-private.

### Dynamic import for circular dependency in `static/js/sampler.js:156-157`
- **Code**: `import('./llm-settings.js').then(mod => mod.saveLLMSettings(...))`
- **Issue**: Fragile. If the module fails to load, the error is silently swallowed. Consider a shared event bus or callback registration.

### Modal delete handler duplicates `deleteCharacter` in `static/js/modal.js:307-333`
- **Issue**: Manually deletes a character and refreshes state, overlapping with `deleteCharacter()` in `characters.js`. Risks divergent behavior.

### `_chat_jsonl()` iterates `data` twice in `routes/llm.py:36-37`
- **Issue**: `models = sorted(m['id'] for m in data)` uses `m['id']` (will KeyError if missing), while `model_details` uses `m.get('context_length')` (safe). Inconsistent key access.
- **Phase**: 2

### `model_details` possibly dead data in `routes/llm.py:37`
- **Issue**: Computed and returned but may not be used by any frontend code. Worth verifying.
- **Phase**: 2

### Inconsistent error response shape in `routes/llm.py`
- **Lines**: 40 vs 64-66
- **Issue**: `list_models()` returns `{'error': ...}` without an `ok` key. `test_llm()` returns `{'ok': False, 'error': ...}`. Inconsistent response shape within the same module.
- **Phase**: 2

### Dead code in `routes/lorebooks.py:224-227`
- **Code**: `name = (existing.get('name') or row['name'] or '').strip() or row['name']`
- **Issue**: The final `or row['name']` is unreachable. If `row['name']` is truthy, the `.strip()` would never fall through. If falsy, `'' or row['name']` is also falsy.
- **Phase**: 2

### Non-transactional `embed_in_character` in `routes/lorebooks.py:253-273`
- **Issue**: Opens two separate `get_db()` contexts. If deletion fails, the character book has already been embedded but the standalone lorebook is orphaned.

### [DONE] Space-aligned variable assignments in `routes/characters.py:159-161`
- **Code**: `file      =`, `fname     =`, `raw_bytes =`
- **Issue**: Unusual style. No other file uses this alignment.
- **Fix**: Normalized spacing to standard single-space alignment.

### `_char_to_dict()` else branch potentially unreachable in `routes/characters.py:52-56`
- **Issue**: `card_data_fields()` always returns a full dict with defaults, so `if card_data:` on line 52 is True for any dict. The `else` branch (line 55) is only reachable when `card_data` is explicitly `None` or `False`.
- **Phase**: 2

### `os.remove()` without error handling in `routes/personas.py:83`, `routes/characters.py:292`
- **Issue**: No handling if the file is locked or permissions prevent deletion. Not a Python-level bug on macOS/Linux, but worth noting.
- **Phase**: 2

### [DONE] Inconsistent SQL style (`WHERE id = ?` vs `WHERE id=?`)
- **Files**: `routes/messages.py` uses spaces around `=`; all other route files do not.
- **Fix**: Normalized to `WHERE id=?` / `SET content=?` style (no spaces around `=`) to match the rest of the codebase.

### Missing `<noscript>` fallback in `templates/index.html`
- **Issue**: Entire app is an SPA with no content when JavaScript is disabled.
- **Phase**: 3 (template/structural)

### [SKIPPED] Google Fonts external dependency
- **File**: `templates/index.html` (lines 7-9)
- **Issue**: Inter font loaded from Google Fonts. Fails offline. (Intentionally skipped — acceptable trade-off for now.)

### [DONE] `updateContextSizeWarning()` called redundantly in `static/js/llm-settings.js:260`
- **Issue**: Called from `applySettingsToUI()` and also from `loadSamplerSettings()` via init flow. Double-calling is harmless but indicates the init path could be streamlined.
- **Fix**: Removed `updateContextSizeWarning()` from `applySettingsToUI()`; added it to `activatePreset()` which is the only caller that needs it outside the init path.
