"""Function ids become tool names and back, and the gates come from the hook.

Pure apart from `call`, which the caller passes in, so every branch is
testable without an engine.
"""

import fnmatch
import re

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
PRE_CALL = "harness::hook::on-pre-call"   # hyphens, as chapter 7 pins it
GATE = "ladder::gate"


def tool_name(function_id: str) -> str:
    return function_id.replace("::", "__")


def function_id(tool_name: str) -> str:
    return tool_name.replace("__", "::")


def tool_list(function_ids: list[str], call) -> list[dict]:
    """One tool per granted id. A grant error is refused here, at turn start,
    because it should never be the model's to find."""
    tools = []
    for fid in function_ids:
        name = tool_name(fid)
        if not TOOL_NAME.match(name) or function_id(name) != fid:
            raise ValueError(f"{fid} cannot be a tool name and come back unchanged")
        info = call("engine::functions::info", {"function_id": fid}) or {}
        tools.append({"name": name, "description": info.get("description") or fid,
                      "input_schema": info.get("request_schema") or {"type": "object"}})
    return tools


def gates(call, session_id: str) -> list[dict]:
    """Whatever observes the stock harness worker's on-pre-call hook for this
    session, in the hook's own order: priority, then function id. Each keeps
    its `functions` globs, because the hook consults it only on those."""
    observers = (call("engine::observers::list", {}) or {}).get("observers") or []
    hooks = [o for o in observers if o.get("function_id") == PRE_CALL
             and globbed((o.get("args") or {}).get("sessions"), session_id)]
    hooks.sort(key=lambda o: ((o.get("args") or {}).get("priority", 0), o["target"]["function_id"]))
    return [{"id": o["target"]["function_id"], "functions": (o.get("args") or {}).get("functions") or []}
            for o in hooks]


def globbed(globs, name: str) -> bool:
    """The harness worker's rule: no globs, or none listed, matches everything."""
    return not globs or any(fnmatch.fnmatchcase(name, glob) for glob in globs)


def consulted(gates: list[dict], function_id: str) -> list[str]:
    """The gates the hook would call for one function, in order."""
    return [g["id"] for g in gates if globbed(g["functions"], function_id)]


def allowed(functions: dict) -> list[str]:
    """The grant as flat ids: allow minus deny. A glob is refused by tool_list."""
    deny = set(functions.get("deny") or [])
    return [fid for fid in functions.get("allow") or [] if fid not in deny]
