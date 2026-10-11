# hands-on-iii

This repo is the companion code for the book *Hands-on iii* by Ian Johnson. The book builds an agentic harness and a factory on iii (pronounced "three eye"), one capability per chapter, and this repo holds the code each chapter adds.

## How the repo is laid out

Each chapter is one commit. Chapters 2 through 12 are tagged `ch02` through `ch12`, so `git log --oneline` reads as the table of contents. Chapter 1 has no code, so its commit holds only this README, the license, and `.gitignore`, with no tag.

Every tag is a working system at that point in the book, and no tag depends on a later chapter. To start from a chapter, check out its tag and bring the system up:

```shell
git checkout ch07
iii compose build   # optional: --up also fetches any missing worker
iii compose --up
```

To see one chapter's change and nothing else, diff two tags. For example, `git diff ch06 ch07` shows everything chapter 7 added.

The iii CLI is the only operator surface. There is no Makefile. Every action is `iii trigger <function> --json '{...}'`, and when logic has to run first, a script in `scripts/` prints the JSON for you to pipe into `iii trigger`.

## Requirements

- iii 0.23.0 or later. Compose needs 0.23, and later releases should stay compatible.
- The `iii-sdk` package at the same version as the engine, for Python and for npm.
- Python 3.11 or later.
- Node 20 or later.
- `git`.

You also need an Anthropic API key from chapter 2. You need `gh` only from chapter 9, and chapter 9 builds a local forge first, so you can finish the book without a GitHub account. The book's front matter, "How to use this book", has the details.

Every worker version is pinned exactly. Do not raise a pin while you follow the book, because the pins change what the code does. Until iii reaches 1.0, the book and this repo move their pins together when a release changes behavior.

## License

MIT. See `LICENSE`.
