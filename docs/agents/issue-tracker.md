# Issue tracker: local files in `.scratch/`

Issues for Calyptus live as Markdown files in `.scratch/`, git-ignored: they exist only in this checkout (treehouse worktrees symlink them).
There is no remote tracker and no CLI.

## Layout

```
.scratch/
  <feature-slug>/
    spec.md                  the feature's spec or plan (optional; PRDs live in docs/prd/)
    issues/
      01-<slug>.md           one ticket per file, numbered in dependency order
      02-<slug>.md
  inbox/
    issues/
      <YYYY-MM-DD>-<slug>.md loose bugs and requests not yet tied to a feature
.out-of-scope/
  <concept>.md               rejected enhancement requests (see the triage skill)
```

A ticket is referred to as `<feature-slug>#NN` (e.g. `auth#03`).
Inbox tickets are referred to by their filename.

## Ticket file

```markdown
# <NN>: <Ticket title>

**Category:** bug | enhancement
**Status:** needs-triage | needs-info | ready-for-agent | ready-for-human | in-progress | in-review | done | wontfix
**Blocked by:** <feature-slug>#NN, … or "None (can start immediately)"

**What to build:** the end-to-end behaviour, from the user's perspective.

- [ ] Acceptance criterion 1

## Comments

### <YYYY-MM-DD> — <author>
<comment body>
```

## Operations

- **Create**: write a new file in `.scratch/<feature-slug>/issues/` with the next free number, or in `.scratch/inbox/issues/` when no feature fits.
- **Change status**: edit the `**Status:**` line in place.
- **Comment**: append a `### <date> — <author>` block under `## Comments`.
- **Find**: `grep -rl '^\*\*Status:\*\* ready-for-agent' .scratch/` (any status works the same way).
- **List open work**: every ticket whose status is not `done` or `wontfix`.
- **Close**: status `done`; rejected work is status `wontfix`.
- **Blocking relations**: the `**Blocked by:**` line; a ticket is on the frontier when every blocker is `done`.
- **Move inbox → feature**: `git mv` the file into the feature's `issues/` with the next free number.

Ignore `done` and `wontfix` tickets when listing or triaging, unless asked about closed work.

## Merge linking

There are no PRs: branches merge into `main` locally after human review.
The merge commit names the ticket (`Closes auth#03`) for humans only: nothing closes it automatically.
Whoever merges sets the ticket's status to `done`.

## When a skill says "publish to the issue tracker"

Write the ticket file as above.

## When a skill says "fetch the relevant ticket"

Read the ticket file, including its `## Comments`.
