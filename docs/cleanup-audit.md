# Cleanup Audit Tracker

This document tracks the read-only cleanup audit completed on 2026-07-11.
Items are grouped by category and ordered by confidence. Check an item only when
the change has been verified and included in its own commit.

Confidence describes how safe the proposed change is:

- **High** — no in-repository caller or behavior depends on it.
- **Medium** — external API consumers, custom themes, old databases, or a product
  decision may depend on it.
- **Low** — the code looks unused internally but remains a reasonable public or
  future-facing interface.

The optional character gallery is not a removal candidate. Gallery entries below
cover only internal leftovers or duplicated behavior that can be cleaned while the
feature remains available.

## Dead code and ineffective styles

- [x] **D1 · High** — Remove the abandoned greeting-navigation implementation:
  `buildGreetingNav()`, `state.greetingIndex`, its writes/resets, and the
  `.greeting-nav*` CSS (`static/js/messages.js`, `static/js/state.js`,
  `static/js/chats.js`, `static/css/style.css`).
- [x] **D2 · High** — Remove the incompatible non-streaming LLM fallback
  `API.chatCompletion()` and require the SSE path in `generateResponse()`
  (`static/js/api.js`, `static/js/request-builder.js`).
- [x] **D3 · High** — Remove unused `import shared` statements from
  `routes/chats.py` and `routes/lorebooks.py`.
- [ ] **D4 · High** — Remove the unused gallery deep-link
  `openGalleryWithCharacter()` and the now-trivial `editCharacter` forwarding
  shim. This does not remove or disable the gallery.
- [x] **D5 · High** — Simplify `write_character_card()` to return only the CRC;
  stop returning unused PNG bytes and recalculating the same CRC.
- [x] **D6 · High** — Remove the unused successful card result from
  `set_character_book()`.
- [ ] **D7 · High** — Replace unnecessary shared `state.apiEndpoint` and
  `state.apiKeySet` with local values.
- [ ] **D8 · High** — Remove dead standalone lorebook-flyout container/button
  selectors while preserving the live nested list/item/manage selectors.
- [ ] **D9 · High** — Remove unmatched `.settings-help` and
  `.settings-help summary` CSS; keep `.settings-help-vars`.
- [ ] **D10 · High** — Remove unused CSS tokens `--mobile-composer-gap`,
  `--app-bg-blur`, `--app-bg-clear`, and `--gallery-selected-bg`.
- [ ] **D11 · Medium** — Remove unused frontend API surface:
  `getCharacters({archivedOnly:true})` and `API.extractCharacterLorebook()`.
  Retain if external browser modules import `api.js` directly.
- [ ] **D12 · Medium** — Stop selecting/returning `persona_tagline` with every
  message unless an external API client consumes the undocumented field.
- [ ] **D13 · Medium** — Remove redundant `GET /api/messages/<id>/swipes` if
  direct API clients do not use it; chat-message responses already include swipes.
- [ ] **D14 · Medium** — Remove no-op DOM/CSS hooks (`always-visible`,
  `composer-no-chat`, tooltip placement modifiers, `sampler-popover-rows`,
  `settings-card-grid`, `settings-card-header--disclosure`, and
  `chat-flyout-header`) after checking custom-theme compatibility.
- [ ] **D15 · Medium** — Remove `export` from symbols used only inside their own
  module unless external browser modules rely on those exports.
- [ ] **D16 · Low** — Decide whether the internally unused
  `GET /api/characters/<id>` route is a supported REST interface.

## Dependencies and build inputs

- [ ] **DEP1 · Medium** — Remove Docker's `libjpeg-dev` and `zlib1g-dev` if all
  supported platforms use Pillow wheels; otherwise document the source-build
  architecture and install a complete build toolchain.

All direct `pyproject.toml` dependencies are used: Flask, Gunicorn, Pillow,
Requests, and pytest.

## Stale comments

- [ ] **COM1 · High** — Reword `app.py`'s obsolete “with livereload extras”
  comment while retaining the SSE buffering explanation.
- [ ] **COM2 · High** — Rename stale “chat / lorebook flyout” comments to
  “chat / Memory flyout” in `templates/index.html` and `static/js/main.js`.
- [ ] **COM3 · High** — Remove the claim that the optional gallery is the primary
  character-creation path.
- [ ] **COM4 · High** — Remove documentation for the dead gallery deep-link with
  D4.
- [ ] **COM5 · High** — Remove/update stale CSS comments describing superseded
  dropup, flyout-header, and lorebook-flyout arrangements.

No commented-out executable blocks or TODO/FIXME/HACK/XXX markers were found.

## Duplicate and near-duplicate logic

- [ ] **DUP1 · High** — Remove the duplicate `@keyframes spin` declaration.
- [ ] **DUP2 · High** — Remove the second redundant `#send-btn:disabled` block.
- [x] **DUP3 · High** — Share the repeated “detach chats, then delete lorebook”
  sequence between standalone deletion and move-to-character deletion.
- [ ] **DUP4 · High** — Share the repeated active-chat replacement used by
  lorebook selection, Author's Note save, and notice dismissal.
- [ ] **DUP5 · High** — Remove caller-side message clearing/composer updates
  already performed by `renderMessages()`.
- [x] **DUP6 · High** — Consolidate duplicate legacy-setting tests and seed an
  actual legacy DB row so the read-time compatibility path is exercised.
- [ ] **DUP7 · High** — Remove component-specific `[hidden]` rules made redundant
  by the global rule, and decide whether model/sampler popover fade-outs should
  work or be removed.
- [ ] **DUP8 · Medium** — Share lorebook entry-normalization primitives while
  preserving strict import behavior versus lossless card normalization.
- [ ] **DUP9 · Medium** — Share low-level JSON upload decoding across character,
  chat, prompt, and lorebook import routes while retaining endpoint-specific
  validation.
- [ ] **DUP10 · Medium** — Share character serialization/upsert behavior between
  the modal and gallery editors without merging their distinct UI behavior.
- [ ] **DUP11 · Medium** — Decide whether `renameChat()` adds useful domain
  meaning or should delegate to/remove duplication with `updateChat()`.

## Orphaned files and repository debris

- [ ] **O1 · Medium** — Remove or intentionally retain the untracked
  `.playwright-mcp/` QA snapshots; add an ignore rule if they are generated.
- [ ] **O2 · High** — Delete the fully merged local branch
  `fix-sampler-param-names` when no longer needed as a bookmark.
- [ ] **O3 · High** — Remove the redundant `.codex/` ignore entry already covered
  by `.codex`.
- [x] **O4 · High** — Stop `tests/conftest.py` from leaking
  `cozy-test-import-*` temporary directories and clean them up at session end.
- [ ] **O5 · Medium** — Update/link or remove the orphaned `docs/samplers.md`;
  it still documents removed TFS-Z behavior.
- [ ] **O6 · Medium** — Tighten `.dockerignore` so QA artifacts, agent docs, and
  the 2 MB README-only logo do not enter the runtime image unless intentional.

## Dead configuration and perpetual migrations

- [ ] **CFG1 · Medium** — Gate the destructive “one-shot” greeting cleanup in
  `shared.py` with a migration version/sentinel; remove it outright only when
  direct upgrades from pre-fix databases are unsupported.
- [ ] **CFG2 · Medium** — Retire the perpetual
  `DROP INDEX IF EXISTS idx_messages_chat_created` through versioned migration.
- [ ] **CFG3 · Medium** — Migrate/delete legacy `context_max_messages` rows once,
  then remove the response-time tombstone. Do not remove only the `pop()` while
  stale rows can still exist.
- [ ] **CFG4 · Medium** — Remove the pre-v1 `active_samplers` field-name fallback
  if pre-repository databases are unsupported; otherwise document and test it.
- [ ] **CFG5 · Medium** — Decide whether chats should be ordered by activity;
  otherwise remove the dead `updated_at` writes/comment while considering
  external API consumers.
- [ ] **CFG6 · Medium** — Introduce schema-version tracking so one-time schema and
  data migrations have an explicit retirement point.

`show_gallery_button` and the gallery archive/collection routes are explicitly
excluded from removal.

## Debug-state cleanup

- [x] **DBG1 · High** — Accumulate full streamed LLM text only when DEBUG logging
  is enabled; keep counters local to the generator where possible.

No console.log/console.debug/debugger/print/breakpoint/pdb leftovers were found.
`/api/llm/test` and development-only Flask debugging are intentional.

## Inconsistent patterns and one-offs

- [x] **INC1 · High** — Serialize forked chats with the normal chat serializer so
  SQLite integer flags do not leak where the API normally returns booleans.
- [ ] **INC2 · Medium** — Decide whether forking should copy `author_note` and
  `lorebook_notice_dismissed`; then move/share chat creation logic rather than
  implementing it inside `routes/messages.py`.
- [x] **INC3 · High** — Stop swallowing file-delete errors that can resurrect a
  character or orphan a persona avatar; ignore only missing files.
- [x] **INC4 · High** — Access data-path constants consistently through `shared`
  so tests no longer patch both `shared` and `app` globals.
- [x] **INC5 · High** — Make test import isolation override an existing
  `COZY_DATA_DIR` instead of using `setdefault()`.
- [ ] **INC6 · High** — Choose package-style test imports or flat imports; remove
  the current combination of `tests/__init__.py` and `sys.path` mutation.
- [ ] **INC7 · High** — Re-sort sidebar character state after pinning from the
  gallery, matching sidebar pin behavior.
- [x] **INC8 · High** — Return 404 for swipe-list requests targeting nonexistent
  messages if D13 is retained.
- [x] **INC9 · High** — Remove minor Python one-offs: duplicate
  `OSError`/`IOError` handling and unexplained function-local imports.
- [ ] **INC10 · Medium** — Replace native `prompt()` in preset and system-prompt
  creation with the application's styled interaction pattern.
- [ ] **INC11 · Medium** — Route theme loading through the API error-handling
  pattern or at least check `Response.ok`.
- [ ] **INC12 · Medium** — Add `--input-focus-shadow` to Solarized Dark or add a
  fallback so themed checkboxes retain a visible focus state.
- [ ] **INC13 · Medium** — Avoid the independent nested DB connection in
  `set_character_book()` after defining the intended transaction boundary.
- [ ] **INC14 · Medium** — Decide and document deletion semantics for unconstrained
  `persona_id` and `active_lorebook_id`; add FKs via table-rebuild migration if
  appropriate.
- [ ] **INC15 · Medium** — Move import-time directory creation/database migration
  toward explicit application initialization while preserving Gunicorn startup.
- [ ] **INC16 · Medium** — Remove `check_same_thread=False` if no supported caller
  moves a connection across threads.

## Documentation and toolchain drift

- [ ] **DOC1 · High** — Bring `docs/db.md` in sync with `shared.py`: pinned/archive
  columns, collection tables, Author's Note, paired prompt content, preset
  settings JSON, and actual FK declarations.
- [ ] **DOC2 · High** — Use `uv run python app.py` in the custom-data example in
  `docs/run.md`.
- [ ] **DOC3 · High** — Synchronize shared architecture/testing facts between
  `AGENTS.md` and `CLAUDE.md` while retaining tool-specific instructions.
- [ ] **DOC4 · High** — Document Node as required for full frontend test coverage
  or expose request-builder tests as an explicit optional test group.
- [ ] **DOC5 · High** — Pin/document a uv version shared by local development and
  Docker to prevent metadata-only `uv.lock` churn.

## Verification notes

- Flask-decorated route functions, dynamically discovered themes, package marker
  files, CLI-only Gunicorn, and Pillow's `PIL` import were checked and excluded as
  false positives.
- The initial audit could not rerun `uv run pytest` because the execution
  environment rejected the required approval. Each implemented item should run
  the closest relevant checks before its commit.
