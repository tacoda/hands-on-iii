"""Build the target repo's charter: AGENTS.md, then every .md under .agents/.

Only `reader` and `for_repo` touch the disk. `resolve` and `build` take text
and a `read` function, so a test can pass a dict's `get` for a directory.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_DEPTH = 3
MAX_PIECE = 40_000   # one file, with its imports inlined
MAX_TURN = 120_000   # every piece together
IMPORT = re.compile(r"^@(\S+)[ \t]*$", re.MULTILINE)


@dataclass
class Charter:
    pieces: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n\n".join(f"### {name}\n\n{body}" for name, body in self.pieces)


def reader(root: Path):
    def read(path: str) -> str | None:
        try:
            target = (root / path).resolve()
            target.relative_to(root.resolve())  # ValueError if it escapes
            return target.read_text() if target.is_file() else None
        except (OSError, ValueError):
            return None
    return read


def resolve(text, read, problems, seen=frozenset(), depth=0) -> str:
    def inline(match):
        path = match.group(1)
        if path in seen:
            problems.append(f"{path}: import cycle, left as written")
        elif depth >= MAX_DEPTH:
            problems.append(f"{path}: deeper than {MAX_DEPTH}, not followed")
        elif (body := read(path)) is None:
            problems.append(f"{path}: not found inside the repo")
        else:
            return resolve(body, read, problems, seen | {path}, depth + 1)
        return match.group(0)
    return IMPORT.sub(inline, text)


def build(files, read) -> Charter:
    built = Charter()
    spent = 0
    for name, text in files:
        body = resolve(text, read, built.problems, frozenset({name})).strip()
        if len(body) > MAX_PIECE:
            built.problems.append(f"{name}: {len(body)} characters, cut to {MAX_PIECE}")
            body = body[:MAX_PIECE]
        if spent + len(body) > MAX_TURN:
            built.problems.append(f"{name}: over the turn cap, not sent")
            continue
        built.pieces.append((name, body))
        spent += len(body)
    return built


def names(root: Path) -> list[str]:
    found = ["AGENTS.md"] if os.path.lexists(root / "AGENTS.md") else []
    notes = root / ".agents"
    if notes.is_dir():
        found += sorted(p.relative_to(root).as_posix() for p in notes.rglob("*.md"))
    return found


def for_repo(root: Path) -> Charter:
    # Raise rather than return an empty charter: a turn without the repo's
    # rules runs under the wrong ones and nothing in its result says so.
    if not root.is_dir():
        raise ValueError(f"charter: {root}: repo root not found")
    os.listdir(root)  # OSError if the root is unreadable
    read = reader(root)
    files, missing = [], []
    for name in names(root):
        # The list goes through `read` too, so a symlink that leaves the repo reads as missing.
        if (text := read(name)) is None:
            missing.append(f"{name}: not found inside the repo")
        else:
            files.append((name, text))
    built = build(files, read)
    built.problems[:0] = missing
    return built
