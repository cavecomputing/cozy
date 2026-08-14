# AGENTS.md

**The instructions for this repository live in [CLAUDE.md](CLAUDE.md). Read that file before you
change anything here and follow it in full.** It is the single source of truth for this project and
applies to every coding agent, not only to Claude.

This file used to be a byte-identical copy of CLAUDE.md. Keeping two copies in step was a chore that
failed quietly, so it is a pointer now — nothing project-specific is duplicated here.

Two rules are restated below because getting them wrong is destructive and this repository is
public, so anything that lands on `main` is something a stranger may run:

- **Never commit unless you were asked to.** Finish the work, leave it in the working tree, and
  report what changed and how it was verified. The user reviews the diff and decides.
- **Never push.** The user always pushes themselves.

Everything else — how to run the app and its tests, the architecture, the two upgrade mechanisms,
the naming and commit conventions, and the testing gotchas that will otherwise cost you a wrong
answer — is in [CLAUDE.md](CLAUDE.md).
