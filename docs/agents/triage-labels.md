# Triage Labels

The skills speak in terms of five canonical triage roles.
This file maps those roles to the local `.scratch/` tracker. See `docs/agents/issue-tracker.md`.

## Role → ticket file

| Role (in skills)  | Ticket file                        | Meaning                                  |
| ----------------- | ---------------------------------- | ---------------------------------------- |
| `needs-triage`    | `**Status:** needs-triage`         | Maintainer needs to evaluate this issue  |
| `needs-info`      | `**Status:** needs-info`           | Waiting on reporter for more information |
| `ready-for-agent` | `**Status:** ready-for-agent`      | Fully specified, ready for an AFK agent  |
| `ready-for-human` | `**Status:** ready-for-human`      | Requires human implementation            |
| `wontfix`         | `**Status:** wontfix`              | Will not be actioned                     |

Categories (`bug`, `enhancement`) go on the `**Category:**` line.
A ticket carries exactly one category and one status.
Work statuses `in-progress`, `in-review`, `done` follow `ready-for-agent` / `ready-for-human` once work starts.
