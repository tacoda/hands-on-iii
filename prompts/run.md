Make this change in the repository you were given.

You can edit files and run commands. The working directory is this job's own
worktree, on its own branch, so you touch nobody else's checkout.

## The document so far

What was asked, the plan an earlier turn made, and any answer a person gave to
a question. Where a person's answer and the spec disagree, the answer wins.

$document

## How to work

Follow the plan where it holds. Where it is wrong, say so and say what you did
instead.

Do not commit, and do not push. Leave the work in the tree. A later stage runs
the delivery gate and this repository's own hooks over the finished diff.

## If you are blocked

If you are blocked, reply with one line that starts `INTERRUPT:` and asks your
question, and nothing else. Only the opening line counts.

Blocked means one of these:

- The requirements contradict each other.
- A credential you need does not exist.
- The only way forward would destroy data.

Preferences, naming, and anything the codebase already answers do not count.
Decide those yourself, and say what you decided.

## When you are done

Reply with what you built: what you changed, and why. Say where the plan was
wrong. This becomes the work section of the document.
