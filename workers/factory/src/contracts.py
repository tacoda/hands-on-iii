"""What a check's answer has to look like, and what invalidates it.

A contract is a dict and an answer is a string, so everything here is pure.
The stage reads a turn's answer when the turn completes, and
factory::verdict_guard reads the same answer inside the turn, at
harness::hook::post-generate. Both call `read`, so they cannot disagree.

A contract checks that evidence is present, never that it is true.
"""

import re

# A file and a line: README.md:1, src/app.py:42. Prose with no place in it is a mood.
PLACE = r"[\w-]+(?:[./][\w-]+)+:\d+"

CONTRACTS = {
    "proven": {"marker": "PROVEN:", "values": ["yes", "no", "partial"],
               "unparseable": "unproven",
               "needs": {"yes": r"^\s*\$\s+\S"},             # a command line
               "why": "a proof with no command under it is a claim"},
    "verdict": {"marker": "VERDICT:", "values": ["pass", "concerns", "blocker"],
                "unparseable": "unreadable",
                "needs": {"concerns": PLACE, "blocker": PLACE},  # a path:line
                "why": "an objecting review that names no path:line is a mood"},
}

def read(text: str, name: str) -> dict:
    c = CONTRACTS[name]
    found = re.search(rf"^\s*{re.escape(c['marker'])}\s*(\w+)", text, re.M)
    claimed = found.group(1).lower() if found else ""
    if claimed not in c["values"]:
        return {"value": c["unparseable"], "claimed": claimed,
                "why": "no readable answer, and that is never a pass"}
    needed = c["needs"].get(claimed)
    if needed and not re.search(needed, text, re.M):
        return {"value": c["unparseable"], "claimed": claimed, "why": c["why"]}
    return {"value": claimed, "claimed": claimed, "why": ""}


# The metadata names the phase and nothing else, so the guard maps it here.
BY_PHASE = {"prove": "proven", "review": "verdict"}

INTERRUPT = "INTERRUPT:"


def place(text: str) -> str:
    """The first path:line an answer names, or ""."""
    found = re.search(PLACE, str(text or ""))
    return found.group(0) if found else ""


def interrupt(text: str) -> str:
    """The question a turn stopped to ask, or "".

    Only the opening line counts, so a summary that says "no INTERRUPT: needed"
    does not stop a job.
    """
    first = (str(text or "").strip().splitlines() or [""])[0].strip()
    return first[len(INTERRUPT):].strip() if first.startswith(INTERRUPT) else ""


def text_of(data) -> str:
    """The final message's words, from a post-generate input or a turn result.

    `content` is a list of typed blocks, not a string. Thinking blocks are
    skipped: a model reasoning about which verdict to give has not given one.
    """
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    message = (data.get("generated") or {}).get("message") or data
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text") or "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def verdict_guard(data: dict) -> dict:
    name = BY_PHASE.get((data.get("metadata") or {}).get("phase", ""))
    text = text_of(data)
    if not name or not text or interrupt(text):
        return {"decision": "continue"}
    answer = read(text, name)
    return {"decision": "continue",
            "annotations": {f"factory.{name}": answer["value"],
                            "factory.claimed": answer["claimed"]}}
