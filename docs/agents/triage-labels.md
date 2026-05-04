# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps
those roles to the actual label strings used in this repo's issue tracker.

| Role in mattpocock/skills | Label in our tracker   | Status          | Meaning                                  |
| ------------------------- | ---------------------- | --------------- | ---------------------------------------- |
| `needs-triage`            | `needs-triage`         | not yet created | Maintainer needs to evaluate this issue  |
| `needs-info`              | `waiting-for-reporter` | existing        | Waiting on reporter for more information |
| `ready-for-agent`         | `ready-for-agent`      | not yet created | Fully specified, ready for an AFK agent  |
| `ready-for-human`         | `ready-for-human`      | not yet created | Requires human implementation            |
| `wontfix`                 | `wontfix`              | existing        | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"),
use the corresponding label string from the right-hand column.

## Why `needs-info` reuses `waiting-for-reporter`

The repo already has `waiting-for-reporter` and it drives the
stale/unstale automation in `.github/workflows/stale.yml` and
`.github/workflows/unstale.yml`: applying it marks an issue stale after
4 days of silence and closes it 3 days after that, while a reporter
comment removes both `stale` and `waiting-for-reporter` automatically.
The mapping reuses that wiring instead of creating a duplicate
`needs-info` label that would not trigger the same automation.

## Creating the missing labels

Before the first triage run, create the three roles that do not yet have
labels:

```sh
gh label create needs-triage \
  --description "Maintainer needs to evaluate this issue" \
  --color fbca04
gh label create ready-for-agent \
  --description "Fully specified, ready for an AFK agent" \
  --color 0e8a16
gh label create ready-for-human \
  --description "Requires human implementation" \
  --color c5def5
```

Edit this file if the vocabulary changes later.
