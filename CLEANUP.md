# Code Cleanup Backlog

> All items from the original audit (Phases 1–3 + remaining 8) are now **resolved**.

---

## Completed Items

### 1. Inconsistent `request.get_json()` semantics — **DONE** (commit `cd562f7`)
- Replaced all `force=True` calls with `silent=True` across `settings.py`, `messages.py`, `llm.py`
- `llm.py` now returns explicit 400 on missing/invalid JSON body

### 2. `update_character` arbitrary key injection — **DONE** (commit `cd562f7`)
- Added `ALLOWED_UPDATE_KEYS = set(CARD_DATA_DEFAULTS)` in `characters.py`
- `update_character()` now filters incoming keys against the allowlist
- Test added: `test_update_ignores_disallowed_keys` verifies `spec`/`spec_version` are ignored

### 3. Inconsistent 404 error message formats — **DONE** (commit `cd562f7`)
- All 18 generic `"Not found"` messages replaced with entity-specific strings:
  - `"Character not found"`, `"Chat not found"`, `"Persona not found"`,
    `"Lorebook not found"`, `"System prompt not found"`, `"Preset not found"`
- Test assertions updated in `test_routes.py` and `test_presets.py`

### 4. Non-transactional `embed_in_character` — **DONE** (commit `cd562f7`)
- Merged the two `get_db()` contexts into one; `set_character_book()` now runs inside the same context
- If the disk write fails, we return early before deleting the standalone row

### 5. Two separate click handlers on `el.chatHistory` — **DONE** (commit `cd562f7`)
- Merged into a single `addEventListener` with early-return for avatar handling

### 6. Dynamic import `.catch` in `sampler.js` — **DONE** (commit `cd562f7`)
- Added `.catch(err => console.error(...))` to both dynamic import calls

### 7. Modal delete handler duplicates `deleteCharacter` — **DONE** (commit `cd562f7`)
- Modal now imports and calls `deleteCharacter()` from `characters.js`
- Added optional `name` parameter to `deleteCharacter(charId, name)` for custom confirm text
- Removed ~25 lines of duplicated state-cleanup code

### 8. Missing test coverage — **PARTIALLY DONE** (commit `cd562f7`)

Added 11 new tests covering 6 of the 8 areas:

| Area | Status | New Tests |
|------|--------|-----------|
| Settings whitelist / unknown key rejection | **Done** | `TestSettingsWhitelist` (2 tests) |
| Character update basic fields | **Done** | `TestCharacterBasicFieldUpdate` (1 test) |
| Chat rename | **Done** | `TestChatRename` (2 tests) |
| Single character GET | **Done** | `TestGetSingleCharacter` (2 tests) |
| List chats for character | **Done** | `TestListChatsForCharacter` (2 tests) |
| Theme serving precedence | **Done** | `TestThemePrecedence` (2 tests) |
| LLM streaming beyond content-type | Not done | Requires mocking `requests.post` SSE stream |
| `resolveTemplateVariables` unit tests | Not done | Requires Node test runner or Python port |

**Remaining test gaps** (low priority, require significant infrastructure):
- LLM streaming: needs a mock `requests.post` that yields SSE lines; existing tests cover content-type and error cases
- `resolveTemplateVariables`: JS-only function; testing requires either a Node test runner or porting the function to Python
