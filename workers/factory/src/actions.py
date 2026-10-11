"""What a stage does: prepare, a turn, commit, publish, watch, and teardown.

`from_turn` and `per_file` are pure. Every other function talks to the engine
only through the worker (or the `call(function_id, payload)`) it is given, so a
test passes a fake. Every git command goes through `shell::exec`, and every
stage that changes the world checks whether its change already exists.
"""

import re
import sys
from pathlib import Path

import forge
import graph as stage_graph
import jobs

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import charter  # noqa: E402  scripts/, the same charter chapter 5 builds
import turn     # noqa: E402  scripts/, the same send chapter 3 builds

GATE = "ladder::gate"
ENDED = {"completed", "cancelled", "failed"}


def run_stage(job: dict, graph: dict | None = None, worker=None) -> dict:
    stages = (graph or stage_graph.load())["stages"]
    action = (stages.get(job["stage"]) or {}).get("action", "stub")
    if action in ACTIONS:
        if worker is None:
            return {"failed": f"{action} needs the engine"}
        return ACTIONS[action](worker, job, job.get("settings") or {})
    if action != "stub":
        return {"failed": f"no action {action!r} at this tag"}
    # Run it's break: {"stub_refuse": "commit"} makes that stub refuse every time.
    if (job.get("options") or {}).get("stub_refuse") == job["stage"]:
        return {"refused": True, "reason": f"the {job['stage']} stub refuses"}
    return {"stub": job["stage"]}


def from_turn(event: dict) -> dict:
    if event.get("status") == "completed":
        return {"ok": True, "result": event.get("result")}
    return {"failed": event.get("reason") or event.get("status") or "the turn ended"}


def ladder_registered(call) -> bool:
    # on_error: fail_closed covers a hook that runs and fails, not one that is
    # gone: a ladder that is down has no binding, and the call runs unguarded.
    try:
        info = call("engine::functions::info", {"function_id": GATE})
    except Exception:
        return False
    return bool(info) and GATE in str(info) and "function_not_found" not in str(info)


def brief(job: dict) -> str:
    if job.get("brief"):
        # A refusal, word for word: the people who wrote the hook said the most
        # specific thing anyone has said about this diff.
        return job["brief"]
    spec = Path(job["spec"])
    text = (spec if spec.is_absolute() else ROOT / spec).read_text()
    return f"Phase: {job['stage']}. Job: {job['id']}.\n\n{text}"


def build_send(job: dict, session: str, call) -> tuple[dict, str, str]:
    # The job's own worktree once prepare made one: every turn edits only that.
    repo = Path(job.get("path") or job["repo"])
    workspace = str(repo if repo.is_absolute() else (ROOT / repo).resolve())
    send = turn.send_options(job["stage"], workspace)
    built = charter.for_repo(Path(workspace))
    if built.pieces:
        send["options"]["system_prompt"] = built.text()
    meta = send["options"]["metadata"]
    meta |= {"job_id": job["id"], "charter_pieces": len(built.pieces)}
    taken = (call("ladder::list", {"repo": workspace}) or {}).get("withheld")
    if taken is None:
        raise RuntimeError("ladder::list did not answer, so the repo's withheld functions are unknown")
    deny = send["options"]["functions"].setdefault("deny", [])
    deny += [fid for fid in taken if fid not in deny]
    level, mode = turn.oversight()
    meta["oversight"] = level
    # The key is the session id, so a duplicate stage message gets the
    # original turn back with deduplicated: true and starts nothing.
    return ({"session_id": session, "idempotency_key": session,
             "message": brief(job), **send}, level, mode)


def start_turn(job: dict, call) -> dict:
    if not ladder_registered(call):
        # Raise before writing anything: the queue retries, then dead-letters,
        # and no turn starts with no rule in front of it.
        raise RuntimeError(f"{GATE} is not registered, so no rule would refuse a call. "
                           "Start the ladder, then redrive factory.stage.")
    session = job.get("session_id")
    if not session:
        # Every move clears session_id, so one already here belongs to this epoch.
        n = (job.get("turns") or {}).get(job["stage"], 0) + 1
        session = jobs.session_id(job, job["stage"], n)
        job = {**job, "session_id": session, "turns": {**(job.get("turns") or {}),
                                                        job["stage"]: n}}
        jobs.write(job)                  # the record first, then the send
    send, level, mode = build_send(job, session, call)
    # A mode set after the send does not reach the calls the turn already made.
    call("approval::set-mode", {"session_id": session, "mode": mode})
    answer = call("harness::send", send) or {}
    return {"started": session, "oversight": level,
            "deduplicated": bool(answer.get("deduplicated"))}


# ------------------------------------------------------------ the engine

def invoke(worker, function_id: str, payload: dict, timeout_ms: int = 120000) -> dict:
    """One call through the worker, as {ok, value} or {ok: False, error}."""
    try:
        answer = worker.trigger({"function_id": function_id, "payload": payload,
                                 "timeout_ms": timeout_ms})
    except Exception as exc:
        error = f"{function_id}: {type(exc).__name__}: {exc}"
        return {"ok": False, "error": error, "output": error}
    value = answer.get("payload", answer) if isinstance(answer, dict) else {}
    value = value if isinstance(value, dict) else {"value": value}
    if value.get("error") and "exit_code" not in value:
        error = f"{function_id}: {value['error']}"
        return {"ok": False, "error": error, "output": error}
    return {"ok": True, "value": value}


def run(worker, program: str, args: list[str], cwd: str) -> dict:
    # shell::exec is not a shell: `command` is one program and `args` a list,
    # so `git add -A && git commit` as one command spawns nothing (S216).
    answer = invoke(worker, "shell::exec", {"command": program, "args": list(args), "cwd": cwd})
    if not answer["ok"]:
        return answer
    value = answer["value"]
    output = f"{value.get('stdout') or ''}{value.get('stderr') or ''}"
    # A command that ran and failed comes back as a successful call, and a
    # timeout has exit_code null, so `or 0` would read it as a success.
    if value.get("timed_out") or value.get("exit_code") != 0:
        return {"ok": False, "output": output, "error": output}
    return {"ok": True, "output": output}


def perform(worker, calls: list) -> tuple[list, dict]:
    """Make the calls a forge driver asked for. The answers, and the first
    failure the driver did not mark as allowed."""
    answers = []
    for one in calls:
        answer = invoke(worker, one["function_id"], dict(one["payload"]))
        answers.append(answer)
        if not answer["ok"] and not one.get("allow_failure"):
            return answers, answer
    return answers, {}


# --------------------------------------------------------------- prepare

def branch_name(job: dict, settings: dict) -> str:
    # The id8 stops the second run of a spec failing with W120: branch already exists.
    slug = re.sub(r"[^a-z0-9]+", "-", forge.title_of(job).lower()).strip("-")[:40].strip("-")
    return f"{settings.get('branch_prefix') or 'factory/'}{slug or 'job'}-{job['id'][:8]}"


def holder(job: dict, settings: dict) -> str:
    """Why this job may not take a worktree yet, or "".

    Counted from the job store, which knows which jobs are live, where a lock
    file can go stale with nothing to say so. A repository with no prepare
    allocates nothing two jobs could fight over, so it is not counted.
    """
    if not str(settings.get("prepare") or "").strip():
        return ""
    limit = int(settings.get("concurrency") or 1)
    live = [j for j in jobs.all_jobs()
            if j["id"] != job["id"] and j.get("repo") == job["repo"]
            and j.get("worktree_id") and j["stage"] not in stage_graph.TERMINAL]
    if len(live) < limit:
        return ""
    held = ", ".join(f"job {j['id'][:8]} at {j['stage']}" for j in live)
    return (f"{job['repo']} allows {limit} live job(s) and {held} holds it. Wait for "
            "it to finish, or raise concurrency for this repository in settings/repos.toml")


def prepare(worker, job: dict, settings: dict) -> dict:
    if not job.get("worktree_id"):
        busy = holder(job, settings)
        if busy:
            return {"ok": False, "error": busy}
        branch = branch_name(job, settings)
        made = invoke(worker, "worktree::create", {
            "repo_path": job["repo"], "session_id": job["id"], "branch": branch,
            "base_ref": settings.get("base") or "main"})
        if not made["ok"]:
            return {"ok": False, "error": f"worktree::create failed: {made['error']}"}
        value = made["value"]
        if not value.get("worktree_id") or not value.get("path"):
            return {"ok": False, "error": "worktree::create returned no worktree_id or path"}
        job = {**job, "worktree_id": value["worktree_id"], "path": value["path"],
               "branch": branch}
        # The record first: a redelivery then claims this tree again instead
        # of creating a second one, which would fail with W120.
        jobs.write(job)
    record = {key: job[key] for key in ("worktree_id", "path", "branch")}
    # The claim names the job, not a process, so the same job can claim its
    # own tree again after a restart.
    claimed = invoke(worker, "worktree::claim",
                     {"worktree_id": job["worktree_id"], "session_id": job["id"]})
    if not claimed["ok"]:
        return {"ok": False, "error": f"worktree::claim failed: {claimed['error']}",
                "record": record}
    script = str(settings.get("prepare") or "").strip()
    if script:
        # A person wrote it as a shell string, so it gets a shell, explicitly.
        ran = run(worker, "sh", ["-c", script], job["path"])
        if not ran["ok"]:
            # No turn can work where the stack will not come up.
            return {"ok": False, "error": f"prepare failed: {ran['error']}", "record": record}
    return {"ok": True, "record": record}


# ------------------------------------------------------------ the commit

DIFF_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")


def per_file(diff: str) -> list[tuple[str, str]]:
    """A unified diff split at file boundaries: each path with its added lines.

    Only added lines, because removing a credential must not be refused for
    containing one.
    """
    files: dict[str, list[str]] = {}
    current, in_hunk = None, False
    for line in diff.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            current, in_hunk = header.group(2), False
            files.setdefault(current, [])
        elif line.startswith("@@"):
            in_hunk = True
        elif current and in_hunk and line.startswith("+"):
            files[current].append(line[1:])
    return [(path, "\n".join(added)) for path, added in files.items()]


def base_ref(job: dict) -> str:
    # origin/main does not resolve in a repository with no remote, and a gate
    # diffing against it would read only the staged half.
    remote, base = forge.for_job(job).remote, forge.base_of(job)
    return f"{remote}/{base}" if remote else base


def rung_four(worker, job: dict, tree: str, message: str) -> tuple[str, str]:
    """The delivery gate. Returns (refusal, problem), and only ("", "") commits.

    It fails closed: a ladder it cannot reach, or a diff it cannot read, is a
    problem, because an empty refusal would read exactly like a clean change.
    """
    parts = []
    # Against the base as well as the index: the run phase holds shell::exec,
    # so a turn can commit by itself, and the staged half alone would be empty.
    for args in (["diff", f"{base_ref(job)}...HEAD"], ["diff", "--cached"]):
        got = run(worker, "git", args, tree)
        if not got["ok"]:
            return "", (f"the delivery gate could not read the change: git {' '.join(args)} "
                        f"failed: {got.get('error')}")
        parts.append(got["output"])
    # One call per file with the real path, so a path-scoped rule can name the
    # file. The message goes with every file, because an escape can live there.
    for path, added in per_file("\n".join(parts)) or [("", "")]:
        content = f"{added}\n{message}" if added else message
        answer = invoke(worker, "ladder::evaluate", {"repo": tree, "path": path,
                                                     "content": content, "rung": 4},
                        timeout_ms=60000)
        if not answer["ok"]:
            return "", ("the delivery gate could not reach the ladder, so nothing "
                        f"checked this change: {answer['error']}")
        decision = answer["value"].get("decision")
        if decision == "deny":
            return str(answer["value"].get("reason") or f"rung 4 refused {path}"), ""
        if decision != "continue":
            return "", f"the ladder gave no decision for {path or 'the commit message'}"
    return "", ""


def commit_message(job: dict) -> str:
    return forge.title_of(job)


def push(worker, tree: str, remote: str, branch: str) -> dict:
    head = run(worker, "git", ["rev-parse", "HEAD"], tree)
    if not head["ok"]:
        return {"ok": False, "error": f"git rev-parse HEAD failed: {head['error']}"}
    there = run(worker, "git", ["ls-remote", remote, f"refs/heads/{branch}"], tree)
    if there["ok"] and there["output"].split()[:1] == [head["output"].strip()]:
        # A redelivered stage: the remote already holds this commit.
        return {"ok": True, "committed": True, "pushed": False}
    pushed = run(worker, "git", ["push", "-u", remote, branch], tree)
    if not pushed["ok"]:
        # A problem, never a refusal: no revision fixes a remote.
        return {"ok": False, "error": f"git push failed: {pushed['error']}"}
    return {"ok": True, "committed": True, "pushed": True}


def delivered(worker, job: dict, tree: str, branch: str) -> dict:
    """Nothing staged after add -A: the commit is already on the branch, from an
    earlier delivery or from the turn. A branch with nothing on it is a problem."""
    ahead = run(worker, "git", ["rev-list", "--count", f"{base_ref(job)}..HEAD"], tree)
    if not ahead["ok"] or not ahead["output"].strip().isdigit():
        return {"ok": False, "error": f"could not count the branch's commits: {ahead.get('error')}"}
    if int(ahead["output"].strip()) == 0:
        return {"ok": False, "error": "the branch has no commits, so there is nothing to deliver"}
    remote = forge.for_job(job).remote
    return push(worker, tree, remote, branch) if remote else {"ok": True, "committed": True}


def commit(worker, job: dict, settings: dict) -> dict:
    tree, branch = str(job.get("path") or ""), str(job.get("branch") or "")
    if not tree or not branch:
        return {"ok": False, "error": "the job has no worktree, so there is nothing to commit"}
    message = commit_message(job)
    run(worker, "git", ["add", "-A"], tree)

    refusal, problem = rung_four(worker, job, tree, message)
    if problem:
        return {"ok": False, "error": problem}
    if refusal:
        return {"ok": True, "refused": True, "refusal": refusal}

    if run(worker, "git", ["diff", "--cached", "--quiet"], tree)["ok"]:
        return delivered(worker, job, tree, branch)
    committed = run(worker, "git", ["commit", "-m", message], tree)
    if not committed["ok"]:
        return {"ok": True, "refused": True,
                "refusal": committed["output"][:4000]}

    remote = forge.for_job(job).remote
    return push(worker, tree, remote, branch) if remote else {"ok": True, "committed": True}


# ----------------------------------------------------- publish and watch

def request_body(job: dict) -> str:
    spec = Path(job["spec"])
    return (spec if spec.is_absolute() else ROOT / spec).read_text()


def publish(worker, job: dict, settings: dict) -> dict:
    if job.get("request"):
        # A rework pushed to the same branch, so the request updated itself.
        return {"ok": True, "opened": False}
    driver = forge.for_job(job)
    # A crash can land between opening a request and recording it, so look first.
    answers, failed = perform(worker, driver.find_calls(job))
    if failed:
        return {"ok": False, "error": f"could not look for an open request: {failed['error']}"}
    found = driver.found(job, answers)
    if found:
        return {"ok": True, "opened": False, "record": {"request": found}}
    answers, failed = perform(worker, driver.open_calls(job, request_body(job)))
    if failed:
        return {"ok": False, "error": f"could not open a request: {failed['error']}"}
    request = driver.opened(job, answers)
    if request.get("error"):
        return {"ok": False, "error": f"could not open a request: {request['error']}"}
    return {"ok": True, "opened": True, "record": {"request": request}}


def outcome_of(state: dict) -> str:
    if state.get("merged"):
        return "merge"
    return "close" if state.get("state") == "closed" else ""


def watch(worker, job: dict, settings: dict) -> dict:
    if not job.get("request"):
        return {"outcome": ""}
    driver = forge.for_job(job)
    answers, failed = perform(worker, driver.view_calls(job))
    # A read that failed is a person who has not acted yet, never a landing.
    return {"outcome": "" if failed else outcome_of(driver.state(job, answers))}


# -------------------------------------------------------------- teardown

def teardown(worker, job: dict) -> dict:
    """Release the claim at a terminal stage, and remove a landed or closed tree.

    A failed job keeps its tree on disk, because the first thing a person wants
    after a failure is what the turn left behind.
    """
    if not job.get("worktree_id"):
        return {"did": []}
    tree = job["worktree_id"]
    released = invoke(worker, "worktree::release", {"worktree_id": tree, "session_id": job["id"]})
    did = ["released" if released["ok"] else f"release failed: {released['error']}"]
    if job["stage"] in ("landed", "closed"):
        # force: a squash merge leaves the branch outside the base's ancestry,
        # and remove would refuse with W221.
        removed = invoke(worker, "worktree::remove", {"worktree_id": tree, "force": True})
        did.append("removed" if removed["ok"] else f"remove failed: {removed['error']}")
    return {"did": did}


ACTIONS = {"prepare": prepare, "commit": commit, "publish": publish, "watch": watch}
