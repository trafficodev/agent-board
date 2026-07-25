# Agent Board — project instructions

## Always commit and push

Every turn that writes to files in this repo must commit and push its own
work on the current branch (`dev` unless explicitly told otherwise) before
reporting completion. Stage only the files changed in that turn — never
`git add -A`/`git add .`. Never commit or push to `main` without the user's
explicit, in-turn permission; if starting from `main`, switch to `dev`
first (create it from `main` if it doesn't exist).

This applies the same way regardless of which provider/model is doing the
work (Claude, Codex, Gemini) — keep it in parity across
`CLAUDE.md`/`AGENTS.md`/`GEMINI.md`.
