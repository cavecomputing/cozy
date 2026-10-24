# AGENTS.md

**The instructions for this repository live in [CLAUDE.md](CLAUDE.md). Read that file before you
change anything here and follow it in full.** It is the single source of truth for this project and
applies to every coding agent, not only to Claude.

This file used to be a byte-identical copy of CLAUDE.md. Keeping two copies in step was a chore that
failed quietly, so it is a pointer now — nothing project-specific is duplicated here.

One rule is restated below because this repository is public, so anything that lands on `main` is
something a stranger may run:

- **Commit and push finished work to `main` without waiting to be asked**, once its tests pass, and
  only the files belonging to the task. Then report what changed, how it was verified, and the
  commit hashes. The user reviews after it lands.

Everything else — how to run the app and its tests, the architecture, the two upgrade mechanisms,
the naming and commit conventions, and the testing gotchas that will otherwise cost you a wrong
answer — is in [CLAUDE.md](CLAUDE.md).
