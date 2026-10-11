---
id: record-is-read-only
layer: team
description: A turn never writes the record, the job files, or anything else under state/
why: A turn that can write the record can rewrite what the record says it did.
rung: [3]
policy: refuse
---

The factory and the audit worker own `state/`. A turn reads its job's
worktree and writes only there.

`record-is-read-only.py` beside this file is the predicate. A `coder::*` path
is resolved against the worktree and refused when it lands under this
repository's `state/`. A `shell::exec` command is refused when its text names
`state/`. Rung 3 does not parse the shell, so a command that builds the path
at runtime passes; the record head in every request body covers that case.
It runs alone: `python3 rules/record-is-read-only.py 'cat state/audit/audit.jsonl'`.
