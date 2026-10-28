---
id: no-self-answer
layer: team
description: A turn never answers a hold
why: A turn that settles its own hold has removed the person the hold asked for.
rung: [3]
policy: refuse
---

A hold waits for a person. A turn may not settle one, its own or another's,
through the engine or through the page's API.

`no-self-answer.py` beside this file is the predicate. It reads command text,
and it does not parse the shell, so it is a convention on the engine path.
The control is the `#rbac` listener (chapter 14). It runs alone:
`python3 rules/no-self-answer.py some_file.sh`.
