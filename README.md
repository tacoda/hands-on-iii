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

Running a tag changes the working tree. The configuration worker writes each worker's settings file into `config/`, and git ignores the ones the repo does not track. Your own edits from a chapter's "Run it" stay behind too. So stop the engine before you move, then check out with `-f`. That drops your edits to tracked files and replaces any untracked file the next tag tracks:

```shell
iii trigger compose::stop
git checkout -f ch08
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

## After the book

Chapter 13 takes the build to other domains. Search before you build, in two steps. First ask the engine what is already on your bus, because a worker installed for one job may answer the next. Then search the public registry, which lists the workers you have not installed:

```shell
iii trigger engine::functions::list search=fetch
iii trigger directory::registry::workers::list search=fetch
iii trigger directory::registry::workers::info name=scrapling
iii trigger compose::add worker=fp@0.2.6
```

The first call searches only what is registered now. The next two need the stock `iii-directory` worker, which proxies the registry behind workers.iii.dev. The `info` call returns one worker's README, config keys, and API reference, even before you install it. Then `compose::add` writes the exact version into `worker-compose.yaml` and restarts the project. Pin the version with `@`. The `iii-sandbox` worker is the exception: it is part of the engine, so enable it under `engine` in `worker-compose.yaml`, not with `compose::add`.

After install, `iii trigger <function> --help` prints one function's schema from the running engine, and `directory::skills::get` reads the worker's skill doc under `~/.iii/skills/<worker>/`. Read its Boundaries section before you grant a function, because that section names what needs approval.

`docs/worksheet.md` is the chapter's "Run it on your own desk" sheet as a file to fill in. `scripts/record-counts.sh` is the chapter's `fp::pipe` count of the record's entries per kind. It needs the stock `fp` worker and a running engine.

## License

MIT. See `LICENSE`.
