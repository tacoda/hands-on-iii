"""The page's routes: one function and one observer each, served by the stock
http worker on 127.0.0.1:3111 (chapter 16).

Every route checks the password first, against the same bcrypt hash as the
Caddyfile. Caddy checks it too, so a caller that skips Caddy, such as a turn's
shell on this machine, still needs it. The routes add no state: each one calls
a function the factory, the ladder, a gate, or the record already serves.
"""

import base64
import os
from pathlib import PurePosixPath

import bcrypt

USER = "operator"
known: set[str] = set()                  # headers that passed, so polls skip bcrypt


def password_hash() -> bytes:
    # The same hash as the Caddyfile. Read per check, so a test can set it.
    return os.environ.get("WEB_PASSWORD_HASH", "").encode()


def repos() -> set[str]:
    return {r.strip() for r in os.environ.get("FACTORY_REPOS", "").split(",") if r.strip()}


def signed_in(headers: dict) -> bool:
    header = headers.get("authorization", "")
    if header in known:
        return True
    hashed = password_hash()
    if not header.startswith("Basic ") or not hashed:
        return False                     # no hash configured opens nothing
    try:
        user, _, password = base64.b64decode(header.removeprefix("Basic ")).decode().partition(":")
        good = user == USER and bcrypt.checkpw(password.encode(), hashed)
    except ValueError:
        return False                     # not base64, not UTF-8, or not a bcrypt hash
    if good:
        known.add(header)
    return good


def ok(body) -> dict:
    return {"status_code": 200, "body": body}


def bad(status_code: int, error: str) -> dict:
    return {"status_code": status_code, "body": {"error": error}}


def route(worker, method: str, path: str, function_id: str, handler):
    def guarded(request: dict) -> dict:
        if not signed_in(request.get("headers") or {}):
            return bad(401, "a password is required")
        try:
            return handler(request)
        except Exception as exc:
            print(f"{function_id}: {exc!r}", flush=True)   # the detail stays in the server's log
            return bad(502, "the factory did not answer; see the server log")
    worker.register_function(function_id, guarded)
    return worker.observe(
        {"function_id": "http::on-request", "args": {"method": method, "path": path}}, function_id)


def register(worker, call) -> list:
    """Every route, as observer handles. main.py waits for each to be ready."""

    def holds_resolve(request: dict) -> dict:
        body = request.get("body") or {}
        decision, reason = body.get("decision"), str(body.get("reason") or "")
        if decision not in ("allow", "deny"):
            return bad(400, "decision is allow or deny")
        if decision == "deny" and not reason.strip():
            return bad(400, "a deny needs a reason")
        kind = body.get("kind")
        if kind not in ("gate", "ladder"):
            return bad(400, "kind is gate or ladder")
        answer = {"session_id": body.get("session_id"), "decision": decision, "reason": reason,
                  "function_call_id": body.get("function_call_id")}
        # Two literal ids, so chapter 12's wiring check sees both calls.
        if kind == "gate":
            return ok(call("approval::resolve", answer))
        return ok(call("ladder::resolve", answer))

    def holds_list(request: dict) -> dict:
        gate = (call("approval::list-pending", {}) or {}).get("pending") or []
        ladder = (call("ladder::holds", {}) or {}).get("holds") or []
        return ok({"holds": [{**h, "kind": "gate"} for h in gate]
                            + [{**h, "kind": "ladder"} for h in ladder]})

    def job(request: dict) -> dict:
        job_id = str((request.get("query_params") or {}).get("id") or "")
        if not job_id:
            return bad(400, "id is required")
        return ok(call("audit::read", {"subject": job_id}))

    def submit(request: dict) -> dict:
        body = request.get("body") or {}
        if body.get("repo") not in repos():
            # Otherwise the password would let anyone point the factory at any path.
            return bad(400, "repo is not in FACTORY_REPOS")
        spec = str(body.get("spec") or "").strip()
        if not spec:
            return bad(400, "spec is required")
        parts = PurePosixPath(spec).parts
        if parts[:1] != ("specs",) or ".." in parts:
            # factory::submit reads any file it can, so the page may name only specs/.
            return bad(400, "spec must be a path under specs/")
        return ok(call("factory::submit", {"spec": spec, "repo": body["repo"]}))

    def health(request: dict) -> dict:
        missing = []
        for function_id in ("ladder::gate", "audit::append"):
            try:
                call("engine::functions::info", {"function_id": function_id})
            except Exception:
                missing.append(function_id)
        return {"status_code": 503, "body": {"ok": False, "missing": missing}} if missing else ok({"ok": True})

    return [
        route(worker, "GET", "/api/jobs", "factory::http-jobs",
              lambda request: ok(call("factory::jobs", {}))),
        route(worker, "POST", "/api/holds/resolve", "factory::http-resolve", holds_resolve),
        route(worker, "GET", "/api/holds", "factory::http-holds", holds_list),
        route(worker, "GET", "/api/job", "factory::http-job", job),
        route(worker, "POST", "/api/jobs", "factory::http-submit", submit),
        route(worker, "GET", "/api/health", "factory::http-health", health),
    ]
