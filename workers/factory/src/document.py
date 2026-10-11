"""One markdown file per job, state/documents/<id>.md, a section per phase.

It starts as a copy of the job's spec, each phase adds one section under a
marker such as `<!-- factory:plan -->`, and the whole file becomes the request
body. The spec in specs/ is the input of record and is never edited; only this
working copy changes. Everything but `folder`, `load`, and `save` is pure text.
"""

import os
import re
import tempfile
from pathlib import Path

import jobs

ROOT = Path(__file__).resolve().parents[3]

TITLES = {"spec": "What was asked", "plan": "The plan", "work": "What was built",
          "answer": "The question, and its answer", "proof": "What was proved",
          "review": "What review found"}

# The marker, not the heading, names a section, so a phase can retitle its
# heading without the next phase losing it.
MARKER = re.compile(r"^<!-- factory:([a-z-]+) -->$", re.M)


def marker(name: str) -> str:
    return f"<!-- factory:{name} -->"


def start(spec: str) -> str:
    return f"{marker('spec')}\n{spec.strip()}\n"


def _spans(text: str) -> list[tuple[str, int, int, int]]:
    """(name, start, body start, end) per section. A section starts at the
    heading line just above its marker, so a body never swallows the next
    section's heading."""
    found = []
    for mark in MARKER.finditer(text):
        begin = mark.start()
        line = text.rfind("\n", 0, max(begin - 1, 0)) + 1
        if begin and text[line:begin - 1].startswith("## "):
            begin = line
        found.append([mark.group(1), begin, mark.end(), len(text)])
    for this, after in zip(found, found[1:]):
        this[3] = after[1]
    return [tuple(span) for span in found]


def sections(text: str) -> dict[str, str]:
    return {name: text[body:end].strip() for name, _, body, end in _spans(text)}


def missing(text: str, names: list[str]) -> list[str]:
    # An empty section is not a section.
    have = sections(text)
    return [name for name in names if not have.get(name)]


def put(text: str, name: str, body: str) -> str:
    """Add a section, or replace it in place: two "What was built" sections
    would leave a reviewer guessing which is current."""
    block = f"## {TITLES.get(name, name)}\n{marker(name)}\n\n{str(body or '').strip()}\n"
    for found, begin, _, end in _spans(text):
        if found == name:
            rest = text[end:].lstrip("\n")
            return text[:begin] + block + ("\n" + rest if rest else "")
    return f"{text.rstrip()}\n\n{block}" if text.strip() else block


# ---------------------------------------------------------------- the file

def folder() -> Path:
    # Beside the job records, so a test that moves FACTORY_JOBS moves these too.
    return Path(os.environ.get("FACTORY_DOCUMENTS") or jobs.folder().parent / "documents")


def path(job: dict) -> Path:
    return folder() / f"{job['id']}.md"


def load(job: dict) -> str:
    try:
        return path(job).read_text()
    except FileNotFoundError:
        spec = Path(job["spec"])
        text = start((spec if spec.is_absolute() else ROOT / spec).read_text())
        save(job, text)
        return text


def save(job: dict, text: str) -> None:
    where = folder()
    where.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=where, suffix=".tmp", delete=False) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path(job))
