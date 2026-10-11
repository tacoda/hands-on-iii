"""The ladder's decisions. Values in, values out.

`decide` is rung 3: rules and a proposed write in, the hook's answer out. It
does no I/O, so every branch is testable without a running turn. `permits` is
rung 2 over the target repository's own `.agents/settings.json`, and
`withheld` is the rung-1 part of that file. Only `read_permissions` and `run`
touch the disk, and both are passed to or called beside the pure parts.
"""

import fnmatch
import importlib.util
import json
import re
from pathlib import Path

# Claude Code's tool names, and the iii functions that do the same job. A
# repository writes `Bash`; the engine runs `shell::exec`. A name missing here
# is reported, never guessed at.
TOOLS = {
    "Bash": ("shell::exec", "shell::exec_bg", "shell::status", "shell::list", "shell::kill"),
    "Read": ("coder::read-file",),
    "Write": ("coder::create-file", "shell::fs::write"),
    "Edit": ("coder::update-file",),
    "MultiEdit": ("coder::update-file",),
    "Grep": ("coder::search",),
    "Glob": ("coder::list-folder", "coder::tree"),
}
SHELL = ("shell::exec", "shell::exec_bg")
PATH_KEYS = ("path", "file_path", "file", "target")
CONTENT_KEYS = ("content", "new_string", "text", "replacement")
ENTRY = re.compile(r"^\s*([A-Za-z]+|[\w-]+::[\w:*-]+)\s*(?:\((.*)\))?\s*$")
SETTINGS = Path(".agents") / "settings.json"


def write_of(call: dict) -> dict:
    """A call's arguments read as a path and a content. A shell command string
    is content, so the predicate reads the command text. This does not parse
    the shell to find the file a command would write."""
    function_id = str(call.get("function_id") or "")
    arguments = call.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    path = next((str(arguments[k]) for k in PATH_KEYS if arguments.get(k)), "")
    texts = [str(arguments[k]) for k in CONTENT_KEYS if isinstance(arguments.get(k), str)]
    command = arguments.get("command")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if function_id in SHELL and command:
        args = arguments.get("args")
        texts.insert(0, " ".join([str(command), *map(str, args)]) if isinstance(args, list)
                     else str(command))
    # coder::create-file and coder::update-file take a batch. Reading only the
    # top level would wave every batch through.
    for item in arguments.get("files") or []:
        if isinstance(item, dict):
            path = path or str(item.get("path") or "")
            texts += [str(item[k]) for k in CONTENT_KEYS if isinstance(item.get(k), str)]
    return {"function_id": function_id, "path": path, "content": "\n".join(texts)}


# ---------------------------------------------------------------- rung 2

def read_permissions(repo: str) -> dict:
    """The target repository's `permissions`. A missing file declares nothing.
    A file that is not JSON raises, so the gate fails closed and says so."""
    if not repo:
        return {}
    target = Path(repo) / SETTINGS
    if not target.is_file():
        return {}
    given = json.loads(target.read_text()).get("permissions") or {}
    return {key: [str(e) for e in given.get(key) or []] for key in ("deny", "ask")}


def functions_for(tool: str) -> tuple[str, ...]:
    return (tool,) if "::" in tool else TOOLS.get(tool, ())


def withheld(permissions: dict) -> list[str]:
    """Rung 1: every function a bare `deny` entry such as `Bash` takes away.
    `allow` is never read: a repository must not widen its own grant by
    writing a file."""
    out: list[str] = []
    for entry in permissions.get("deny") or []:
        match = ENTRY.match(entry)
        if match and match.group(2) is None:
            out += [f for f in functions_for(match.group(1)) if f not in out]
    return out


def unmapped(permissions: dict) -> list[str]:
    return [f"permissions entry {e!r} names no function this ladder knows"
            for key in ("deny", "ask") for e in permissions.get(key) or []
            if not (m := ENTRY.match(e)) or not functions_for(m.group(1))]


def matches(entry: str, write: dict, bare: bool) -> bool:
    match = ENTRY.match(entry)
    if not match or write["function_id"] not in functions_for(match.group(1)):
        return False
    pattern = match.group(2)
    if pattern is None:
        return bare
    # The command against the pattern, whole. `pytest && php x` is not `php *`:
    # a matcher that guessed at shell semantics would be a different rule.
    subject = write["content"] if write["function_id"] in SHELL else write["path"]
    return bool(subject) and fnmatch.fnmatchcase(subject.strip(), pattern.strip())


def permits(permissions: dict, write: dict) -> dict | None:
    """Rung 2's answer for one call, or None when no entry is about it. A bare
    `deny` is rung 1's, withheld before the turn, so it is not matched here."""
    for entry in permissions.get("deny") or []:
        if matches(entry, write, bare=False):
            return answer("refuse", "repo-permissions", 2,
                          f"This repository's {SETTINGS} denies `{entry}`, and this call "
                          "matches it. Use another way the repository allows, or say in your "
                          "reply why the work needs it.")
    for entry in permissions.get("ask") or []:
        if matches(entry, write, bare=True):
            return answer("ask", "repo-permissions", 2,
                          f"This repository's {SETTINGS} asks a person before `{entry}`, "
                          "and this call matches it.")
    return None


def repo_permissions(repo: str, write: dict) -> dict | None:
    return permits(read_permissions(repo), write)


# ---------------------------------------------------------------- rung 3

def run(rule, write: dict) -> list[dict]:
    """Run one rule's predicate. It does not catch: a predicate that raises
    reaches the gate's `except`, and the gate refuses as its own failure."""
    spec = importlib.util.spec_from_file_location(f"rule_{rule.id.replace('-', '_')}",
                                                  rule.script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The function id and the turn's root, so a predicate about where a call
    # lands (record-is-read-only) can resolve a relative path.
    context = {"rule": rule.id, "function_id": write.get("function_id", ""),
               "root": write.get("root", "")}
    return list(module.check(write["path"], write["content"], context) or [])


def answer(policy: str, rule_id: str, rung: int, reason: str) -> dict:
    decision = {"refuse": "deny", "ask": "hold"}[policy]
    return {"decision": decision, "reason": reason, "policy": policy,
            "annotations": {"ladder.rule": rule_id, "ladder.rung": rung}}


def reason(rule, finding: dict, path: str) -> str:
    where = f"{path} line {finding.get('line')}" if path else f"line {finding.get('line')}"
    words = [f"{rule.description} ({rule.id}): {finding.get('why', 'a finding')} at {where}.",
             rule.why]
    if rule.escape:
        words.append(f"For a real exception, add `{rule.escape}` to the content and give "
                     "the reason in your summary.")
    return " ".join(w for w in words if w)


def applies(rule, path: str) -> bool:
    # An empty path is checked against every path-scoped rule: no finding can
    # name a file, but none is skipped either.
    return not rule.paths or not path or any(fnmatch.fnmatchcase(path, g) for g in rule.paths)


def decide(rules, write: dict, rung: int, run=run) -> dict:
    path, content = write.get("path") or "", write.get("content") or ""
    if not path and not content:
        return {"decision": "continue"}   # a read, under rules about writes
    warned = []
    for rule in rules:
        if rung not in rule.rungs or not rule.script or not applies(rule, path):
            continue
        if rule.escape and rule.escape in content:
            continue
        findings = run(rule, write)
        if not findings:
            continue
        if rule.policy == "warn":
            warned += [{"rule": rule.id, **f} for f in findings]
            continue
        # The first refuse or ask wins: the model has to solve one either way.
        return answer(rule.policy, rule.id, rung, reason(rule, findings[0], path))
    if warned:
        return {"decision": "continue", "annotations": {"ladder.warned": warned}}
    return {"decision": "continue"}
