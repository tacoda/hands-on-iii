Run this software against the acceptance criteria and report what you saw.

You can run commands. You cannot edit: this phase holds no editing function,
because a check that repairs what it finds reports on a tree nobody else has
seen. The factory resets the worktree after this turn, so anything a command
changes is undone.

## The document so far

What was asked, what was planned, any answer a person gave, and what the run
turn says it built. Check against what was asked, as the answers amend it.

$document

## What to produce

The first line is `PROVEN: yes`, `PROVEN: no`, or `PROVEN: partial`.

Under each criterion you claim, show the command you ran on its own line,
starting with `$ `, and then what it printed:

```
PROVEN: yes

- [x] README.md gains one section about ports.
      $ grep -n "^## Ports" README.md
      12:## Ports
```

A `yes` with no command line under it becomes `unproven`. If you could not
run something, answer `partial` and name what you could not check, and why.
