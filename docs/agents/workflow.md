# Workflow rules

How to work in this repo. Linked from [CLAUDE.md](../../CLAUDE.md).

- **Delivery** — no remote, no PR: `/ship` stops at a reviewed branch, the user reviews the diff, then it merges into `main` locally (`.agents/ship.md`).
- **Dev server** — never run it without asking first.
- **Git stash** — always named: `git stash push -u -m "<name>"` /
  `git stash pop "stash^{/<name>}"`. Never bare stash/pop.
- **Env vars** — only read/edit `.env.example`. Never read `.env.local`.
- **Docs hygiene** — delete it in the code, delete it in the docs: no doc names a
  symbol, file or route that no longer exists. Only `docs/adr/` and `docs/prd/`
  keep history.
- **Prose docs** — one sentence per line (`CONTEXT.md`, map cards, `docs/agents/`), so parallel branches rebase without conflicts.
- **Stale deps** — the post-checkout / post-rewrite hook re-runs the install when a lockfile changed; after a rebase never "fix" a gate failure by hand before it has run.

## Layout

```
.agents/        /ship config (ship.md, verify.md) and repo-setup version
.claude/        skills and ship agents
.scratch/       local issue tracker, see docs/agents/issue-tracker.md
docs/adr/       architecture decisions (created when needed)
docs/prd/       product specs, docs/prd/YYYY-MM-DD-<slug>.md
docs/agents/    how agents work here
scripts/        repo scripts (sync-deps.sh)
```

Source and test folders are added when the spec picks the stack.
