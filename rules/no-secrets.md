---
id: no-secrets
layer: team
description: A credential never reaches a file
why: A leaked key costs a rotation, an incident review, and whatever was done with it first.
rung: [3]
policy: refuse
escape: secret-ok
---

Read a credential from the environment. Never write its value into a file,
a command, or a commit message.

`no-secrets.py` beside this file is the predicate. It runs alone:
`python3 rules/no-secrets.py some_file.py`.
