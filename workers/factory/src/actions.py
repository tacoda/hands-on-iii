"""What a stage does: prepare, a turn, commit, publish, watch, rework, and teardown.

`from_turn`, `per_file`, `render`, and `derive_outcome` are pure. Every other function talks to the engine
only through the worker (or the `call(function_id, payload)`) it is given, so a
test passes a fake. Every git command goes through `shell::exec`, and every
stage that changes the world checks whether its change already exists.
"""

import re
import string
import sys
from pathlib import Path

import contracts
import cost
import document
import forge
import graph as stage_graph
import jobs

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import charter  # noqa: E402  scripts/, the same charter chapter 5 builds
import turn     # noqa: E402  scripts/, the same send chapter 3 builds

GATE = "ladder::gate"
GENESIS = "0" * 64
ENDED = {"completed", "cancelled", "failed"}
# The factory pushes with your credentials, so it is the request's author, and
# its own comments are told apart by this instead.
MARKER = "<!-- factory -->"


def prompts() -> Path:
    return ROOT / "prompts"


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


def phase_of(job: dict, stage: dict | None = None) -> str:
    stage = stage if stage is not None else stage_graph.load()["stages"].get(job["stage"]) or {}
    return str(stage.get("phase") or job["stage"])


def render(template: str, fields: dict) -> str:
    # Plain substitution and nothing more; safe_substitute leaves a $PATH in a
    # spec alone instead of failing the turn.
    return string.Template(template).safe_substitute(fields)


def brief(job: dict, phase: str | None = None) -> str:
    if job.get("brief"):
        # A refusal or a reviewer's comment, word for word: the most specific
        # thing anyone has said about this diff, with no spec beside it.
        return job["brief"]
    text = document.load(job)
    prompt = prompts() / f"{phase or job['stage']}.md"
    if not prompt.is_file():
        return text
    # Read at send time, so an edit to prompts/ needs no restart.
    return render(prompt.read_text(), {"document": text, "base": base_ref(job)})


def stage_level(stage_name: str) -> str | None:
    # settings/oversight.yaml can name a level per stage; the default otherwise.
    return (turn.settings("oversight").get("stages") or {}).get(stage_name)


def build_send(job: dict, session: str, call, phase: str | None = None) -> tuple[dict, str, str]:
    phase = phase or job["stage"]
    # The job's own worktree once prepare made one: every turn edits only that.
    repo = Path(job.get("path") or job["repo"])
    workspace = str(repo if repo.is_absolute() else (ROOT / repo).resolve())
    send = turn.send_options(phase, workspace)
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
    level, mode = turn.oversight(stage_level(job["stage"]))
    meta["oversight"] = level
    # The key is the session id, so a duplicate stage message gets the
    # original turn back with deduplicated: true and starts nothing.
    return ({"session_id": session, "idempotency_key": session,
             "message": brief(job, phase), **send}, level, mode)


def start_turn(job: dict, call, worker=None, stage: dict | None = None) -> dict:
    stage = stage if stage is not None else stage_graph.load()["stages"].get(job["stage"]) or {}
    phase = phase_of(job, stage)
    if not ladder_registered(call):
        # Raise before writing anything: the queue retries, then dead-letters,
        # and no turn starts with no rule in front of it.
        raise RuntimeError(f"{GATE} is not registered, so no rule would refuse a call. "
                           "Start the ladder, then redrive factory.stage.")
    absent = document.missing(document.load(job), list(stage.get("requires") or []))
    if absent:
        # The entry check, before anyone pays for a turn that would run on nothing.
        return {"failed": f"{job['stage']} needs {', '.join(absent)} in the document "
                          "and there is none"}
    session = job.get("session_id")
    if not session:
        # Every move clears session_id, so one already here belongs to this epoch.
        n = (job.get("turns") or {}).get(phase, 0) + 1
        session = jobs.session_id(job, phase, n)
        job = {**job, "session_id": session, "turns": {**(job.get("turns") or {}), phase: n}}
        if stage.get("reset_worktree"):
            taken = snapshot(worker, job) if worker is not None else {
                "ok": False, "error": "no worker to run git with"}
            if not taken["ok"]:
                # A check you cannot undo is a check that can repair.
                return {"failed": f"could not snapshot the worktree before {job['stage']}: "
                                  f"{taken['error']}"}
            job["snapshot"] = taken["tree"]
        jobs.write(job)                  # the record first, then the send
    send, level, mode = build_send(job, session, call, phase)
    # A mode set after the send does not reach the calls the turn already made.
    call("approval::set-mode", {"session_id": session, "mode": mode})
    answer = call("harness::send", send) or {}
    return {"started": session, "oversight": level,
            "deduplicated": bool(answer.get("deduplicated"))}


def section_for(text: str, name: str, answer: dict) -> str:
    """The turn's answer, with what the contract made of it underneath, so a
    downgrade keeps the claim and its correction side by side."""
    if answer["value"] == answer["claimed"]:
        return f"{text.strip()}\n\nContract `{name}`: `{answer['value']}`."
    said = f"`{answer['claimed']}`" if answer["claimed"] else "nothing readable"
    return (f"{text.strip()}\n\nContract `{name}`: `{answer['value']}`. The turn "
            f"answered {said}, and {answer['why']}.")


def finish_turn(worker, job: dict, stage: dict, result: dict) -> dict:
    """What a completed turn means for its stage: reset a check's tree, then an
    interrupt, the exit check, and the contract, in that order."""
    record = {}
    if job.get("snapshot"):
        restored = restore(worker, job)
        record["snapshot"] = None
        if not restored["ok"]:
            return {"failed": f"could not reset the worktree after {job['stage']}: "
                              f"{restored['error']}", "record": record}
    if result.get("failed"):
        return {**result, "record": record}
    text = contracts.text_of(result.get("result"))
    # Before the contract: a turn that stopped to ask has no answer to grade.
    question = contracts.interrupt(text) if stage.get("on_interrupt") else ""
    if question:
        return {"interrupt": question, "record": record | {"question": question}}
    produces = list(stage.get("produces") or [])
    if not produces:
        return {"ok": True, "record": record}
    name = stage.get("contract")
    answer = contracts.read(text, name) if name and text.strip() else None
    body = section_for(text, name, answer) if answer else text
    written = document.put(document.load(job), produces[0], body)
    document.save(job, written)
    absent = document.missing(written, produces)
    if absent:
        return {"failed": f"{job['stage']} produced no {', '.join(absent)}", "record": record}
    if answer is None:
        return {"ok": True, "record": record}
    record["contracts"] = {**(job.get("contracts") or {}), name: answer}
    if answer["value"] == "blocker" and stage.get("on_blocker"):
        # Only a readable blocker reaches here, so it always names a place.
        where = contracts.place(text)
        return {"ok": True, "blocker": where,
                "record": record | {"question": f"{job['stage']} named a blocker at {where}"}}
    return {"ok": True, "record": record}


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


# ------------------------------------------------- checks cannot repair

def snapshot(worker, job: dict) -> dict:
    """The run's uncommitted work as a tree object: no commit, no branch change."""
    tree = str(job.get("path") or "")
    if not tree:
        return {"ok": False, "error": "the job has no worktree"}
    added = run(worker, "git", ["add", "-A"], tree)
    if not added["ok"]:
        return {"ok": False, "error": f"git add -A failed: {added['error']}"}
    written = run(worker, "git", ["write-tree"], tree)
    if not written["ok"] or not written["output"].strip():
        return {"ok": False, "error": f"git write-tree failed: {written.get('error')}"}
    return {"ok": True, "tree": written["output"].strip()}


def restore(worker, job: dict) -> dict:
    """Put the snapshot back: changed and deleted files return, created ones
    go. Ignored files stay. A commit the check made is not undone."""
    tree = str(job.get("path") or "")
    for args in (["read-tree", job["snapshot"]], ["checkout-index", "-a", "-f"],
                 ["clean", "-fd"]):
        done = run(worker, "git", args, tree)
        if not done["ok"]:
            return {"ok": False, "error": f"git {' '.join(args)} failed: {done['error']}"}
    return {"ok": True}


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


# ------------------------------------------------------------ the record

def record(worker, kind: str, subject: str, detail: dict, actor: str = "factory") -> None:
    """One entry, through the audit worker, which is the record's only writer.
    Fails open, and never silently: a missing entry means nothing unless the
    log shows no failure."""
    if worker is None:
        return
    done = invoke(worker, "audit::append", {"kind": kind, "actor": actor,
                                            "subject": subject, "detail": detail},
                  timeout_ms=10000)
    if not done["ok"]:
        print(f"factory: {kind} not recorded: {done['error']}", flush=True)


def entered(worker, job: dict, why: str) -> None:
    """stage.entered after a real transition, with the new epoch."""
    history = job.get("history") or [{}]
    detail = {"stage": job["stage"], "epoch": job["epoch"],
              "from": history[-1].get("from"), "why": why}
    if job["stage"] == "blocked":
        detail["reason"] = why
    record(worker, "stage.entered", job["id"], detail)


def record_head(worker) -> str:
    """The last entry's hash, or genesis for an empty record, or "" when the
    audit worker does not answer."""
    if worker is None:
        return ""
    got = invoke(worker, "audit::summary", {"by": []}, timeout_ms=30000)
    if not got["ok"] or "head" not in got["value"]:
        print(f"factory: no record head: {got.get('error') or 'audit::summary gave none'}",
              flush=True)
        return ""
    return str(got["value"]["head"] or GENESIS)


def anchor(head: str) -> str:
    # A copy of the chain's head outside state/, so a rebuilt chain disagrees
    # with what the forge already holds. verify_audit.py --anchor checks it.
    return f"record head: {head}" if head else "record head: unknown (the audit worker did not answer)"


def model_of(job: dict, stage: dict, event: dict) -> str:
    if event.get("model"):
        return str(event["model"])
    try:
        return str(turn.settings.for_phase(phase_of(job, stage))["model"])
    except (ValueError, KeyError):
        return ""


def metrics(worker, session: str | None) -> dict:
    """The turn's token and cost totals across its session tree, or {}. No
    answer leaves the turn to the router's figure, then unpriced."""
    if worker is None or not session:
        return {}
    answer = invoke(worker, "harness::metrics", {"root_session_id": session}, timeout_ms=10000)
    if not answer["ok"]:
        print(f"factory: no metrics for {session}: {answer['error']}", flush=True)
        return {}
    totals = answer["value"].get("totals")
    return totals if isinstance(totals, dict) else {}


def costed(worker, job: dict, stage: dict, event: dict) -> dict:
    """The job with this turn's cost and its source added, and the cost on the record."""
    spent = cost.turn_cost(event, metrics(worker, job.get("session_id")),
                           model_of(job, stage, event), turn.settings("pricing"))
    spent["session_id"] = job.get("session_id")
    record(worker, "turn.cost", job["id"], spent)
    return {**job, "costs": [*(job.get("costs") or []), spent]}


def budget(job: dict) -> str:
    """Why this job may not start another stage, or "". Read on every check,
    so a raised job_max_usd applies to the next stage."""
    return cost.over_budget(job.get("costs") or [], turn.settings("pricing"))


# ----------------------------------------------------- publish and watch

def request_body(job: dict, head: str = "") -> str:
    # The document the phases built, unchanged: what was asked, planned,
    # built, proved, and found. Then the record's head.
    return f"{document.load(job).rstrip()}\n\n{anchor(head)}\n"


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
    head = record_head(worker)
    answers, failed = perform(worker, driver.open_calls(job, request_body(job, head)))
    if failed:
        return {"ok": False, "error": f"could not open a request: {failed['error']}"}
    request = driver.opened(job, answers)
    if request.get("error"):
        return {"ok": False, "error": f"could not open a request: {request['error']}"}
    record(worker, "published", job["id"], {"request": request.get("url") or request.get("id"),
                                            "forge": driver.name, "record_head": head})
    return {"ok": True, "opened": True, "record": {"request": request}}


def derive_outcome(job: dict, pr: dict) -> dict:
    state = str(pr.get("state") or "").lower()
    if pr.get("merged") or state == "merged":
        return {"outcome": "merge"}
    if state == "closed":
        return {"outcome": "close"}
    for comment in reversed(pr.get("comments") or []):
        if MARKER in str(comment.get("body") or ""):
            continue                                   # the factory's own
        if comment.get("id") == job.get("answered_comment"):
            return {"outcome": ""}                     # already reworked
        return {"outcome": "comment", "brief": comment.get("body"),
                "comment_id": comment.get("id")}
    return {"outcome": ""}


def outcome_of(state: dict) -> str:
    return derive_outcome({}, {**state, "comments": []})["outcome"]


def watch(worker, job: dict, settings: dict) -> dict:
    if not job.get("request"):
        return {"outcome": ""}
    driver = forge.for_job(job)
    answers, failed = perform(worker, driver.view_calls(job))
    if failed:
        # A read that failed is a person who has not acted yet, never a landing.
        return {"outcome": ""}
    pr = dict(driver.state(job, answers))
    said, _ = perform(worker, driver.comment_calls(job))
    pr["comments"] = driver.comments(said)
    found = derive_outcome(job, pr)
    if found["outcome"] != "comment":
        return {"outcome": found["outcome"]}
    return {"outcome": "comment",
            "record": {"comment": {"id": found["comment_id"], "body": found["brief"]}}}


# ---------------------------------------------------- rework and blocked

def rework(worker, job: dict, settings: dict) -> dict:
    """Claim the same tree again, say the comment was read, and make the
    comment the whole brief for run."""
    comment = job.get("comment") or {}
    if not str(comment.get("body") or "").strip():
        return {"ok": False, "error": "rework has no comment to work from"}
    prepared = prepare(worker, job, settings)
    if not prepared.get("ok"):
        return prepared
    # The marker, so the next poll does not read this reply as new feedback.
    reply = f"{MARKER}\nThe factory read this comment and is reworking the change."
    perform(worker, forge.for_job(job).say_calls(job, reply))
    return {"ok": True, "record": {**prepared.get("record", {}), "brief": comment["body"],
                                   "answered_comment": comment.get("id"), "comment": None}}


def hold(worker, job: dict, settings: dict) -> dict:
    # Nothing retries a blocked job. Only factory::answer moves it.
    return {}


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


ACTIONS = {"prepare": prepare, "commit": commit, "publish": publish, "watch": watch,
           "rework": rework, "hold": hold}
