"""The factory reading its own record: a grader, the improve lane, and the wiring check.

`grade` is the evaluator the stock eval worker calls by function id.
`trouble`, `parse`, `as_spec`, and `unwired` are pure: values in, values out.
`start`, `completed`, `accept`, and `wiring` touch the engine only through the
`call(function_id, payload)` they are given, and the disk only under the root
they are given, so a test passes a fake and a temporary folder.

Nothing here applies a change. `accept` writes one spec into specs/ and stops;
the spec goes through factory::submit like any other work.
"""

import json
import re
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import actions
import charter
import contracts
import turn

PHASE = "improve"
RUN_ID = re.compile(r"^[0-9a-f]{8}$")
SESSION = re.compile(r"^s_([0-9a-f]{8})_improve_1$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
LANES = ("charter", "harness", "factory")
ACTIONS = ("add", "improve", "remove", "promote", "demote")
FIELDS = ("lane", "kind", "target", "action", "evidence", "why")

# The ids a person triggers by hand. Nothing in the repo calls them, so the
# wiring check takes this list's word. It is a claim, and you can read it.
OPERATOR = ("factory::submit", "factory::jobs", "factory::answer", "factory::improve",
            "factory::proposals", "factory::accept", "factory::wiring",
            "audit::verify", "ladder::explain")


# ------------------------------------------------------------- the grader

def words(output) -> str:
    """A reply's text, whether the eval worker hands over a string or blocks."""
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return output["text"]
    if isinstance(output, list):
        return "\n".join(words(item) for item in output)
    return contracts.text_of(output)


def grade(payload: dict) -> dict:
    """factory::grade: {output, arguments: {contract, equals}} to {passed, score, reason}.

    Deterministic, because delivery is at-least-once: a grader that answered
    differently on a second delivery would make the report random."""
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    args = dict(data.get("arguments") or {})
    data = {**data, "output": words(data.get("output"))}
    try:
        answer = contracts.read(str(data.get("output") or ""), args["contract"])
        passed = answer["value"] == args["equals"]
    except Exception as exc:  # an evaluator that cannot run fails
        return {"passed": False, "score": 0.0, "reason": f"{type(exc).__name__}: {exc}"}
    # The value after chapter 10's downgrades, never the raw claim.
    reason = f"read as `{answer['value']}`, expected `{args['equals']}`"
    if answer["why"]:
        reason += f": {answer['why']}"
    return {"passed": passed, "score": 1.0 if passed else 0.0, "reason": reason,
            "details": {"value": answer["value"], "claimed": answer["claimed"]}}


# ------------------------------------------------------------- the evidence

def signal(kind: str, what: str, argues: str, jobs=(), count: int = 1, **detail) -> dict:
    return {"kind": kind, "what": what, "argues": argues,
            "jobs": sorted(set(jobs)), "count": count, "detail": detail}


def job_subjects(entries: list[dict]) -> list[str]:
    return [str(e.get("subject") or "") for e in entries
            if JOB_ID.match(str(e.get("subject") or ""))]


def trouble(jobs: list[dict], entries: list[dict], rules: list[dict]) -> list[dict]:
    """What cost something. Each signal says what it argues for, because a
    count alone is not an argument."""
    found = []
    revised = [j for j in jobs if int(j.get("revisions") or 0) > 0]
    if revised:
        found.append(signal(
            "revision", f"{len(revised)} job(s) needed a revision",
            "the repo's commit gate refused work the run phase called finished: "
            "the gate's rule is missing from the charter, or the prose does not carry it",
            [j["id"] for j in revised], sum(int(j["revisions"]) for j in revised)))

    refusals = [{**e, "actor": str(e.get("actor") or "ladder")} for e in entries
                if e.get("kind") == "ladder.refused"]
    by_rule = defaultdict(list)
    for e in refusals:
        by_rule[(e["actor"], str((e.get("detail") or {}).get("rung", "?")))].append(e)
    for (rule, rung), hits in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        found.append(signal(
            "refusal", f"`{rule}` refused {len(hits)} call(s) at rung {rung}",
            "a rule the turns keep hitting: the prose is not stopping it, so it wants "
            "a higher rung", job_subjects(hits), len(hits), rule=rule, rung=rung))

    outcomes = {str((e.get("detail") or {}).get("function_call_id") or ""):
                str((e.get("detail") or {}).get("outcome") or "")
                for e in entries if e.get("kind") == "approval.resolved"}
    held = defaultdict(list)
    for e in entries:
        if e.get("kind") == "approval.held":
            held[str((e.get("detail") or {}).get("function_id") or "?")].append(e)
    for function_id, hits in sorted(held.items(), key=lambda kv: -len(kv[1])):
        answered = Counter(outcomes.get(str((e.get("detail") or {}).get("function_call_id")),
                                        "unanswered") for e in hits)
        found.append(signal(
            "held", f"`{function_id}` waited for a person {len(hits)} time(s): {dict(answered)}",
            "a call held every time is a rule that should `refuse`, or a capability "
            "that should be granted", job_subjects(hits), len(hits),
            function_id=function_id, outcomes=dict(answered)))

    # A rule whose every claimed rung was refused carries none, so it is prose.
    rules = [{**r, "rungs": list(r.get("rungs") or [0])} for r in rules]
    fired = {e["actor"] for e in refusals}
    silent = [r for r in rules if r["id"] not in fired]
    watched = [r for r in silent if max(r["rungs"]) > 0]
    prose = [r for r in silent if max(r["rungs"]) == 0]
    if not jobs:
        return found                     # silence over zero jobs says nothing about any rule
    if watched:
        found.append(signal(
            "never-fired", f"{len(watched)} rule(s) never fired: "
            + ", ".join(r["id"] for r in watched),
            "settled or theater: demote it, or remove it", count=len(watched),
            rules=[r["id"] for r in watched]))
    if prose:
        found.append(signal(
            "unobservable", f"{len(prose)} rule(s) carried only at rung 0: "
            + ", ".join(r["id"] for r in prose),
            "rung 0 refuses nothing, so its silence is not evidence", count=len(prose),
            rules=[r["id"] for r in prose]))
    return found


def as_brief(signals: list[dict], jobs: list[dict]) -> str:
    lines = [f"{len(jobs)} job(s) on record. What cost something:", ""]
    for s in signals:
        lines += [f"### {s['what']}", "", f"- signal: `{s['kind']}`, seen {s['count']} time(s)",
                  f"- argues: {s['argues']}"]
        if s["jobs"]:
            lines.append("- jobs: " + ", ".join(j[:8] for j in s["jobs"][:8]))
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------- proposals

FIELD = re.compile(rf"^\s*[-*]\s*({'|'.join(FIELDS)})\s*:\s*(.*)$", re.I)


def split(text: str) -> list[tuple[str, str]]:
    marks = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.M))
    return [(m.group(1).strip(), text[m.end(): marks[i + 1].start() if i + 1 < len(marks)
                                      else len(text)])
            for i, m in enumerate(marks)]


def fields_of(body: str) -> tuple[dict, str]:
    """The six fields, with indented continuation lines, and the prose left over."""
    found, rest, last = {}, [], None
    for line in body.splitlines():
        match = FIELD.match(line)
        if match:
            last = match.group(1).lower()
            found[last] = match.group(2).strip()
        elif last and line.strip() and line[:1].isspace():
            found[last] = f"{found[last]} {line.strip()}"
        else:
            last = None
            rest.append(line)
    return found, "\n".join(rest).strip()


def traced(evidence: str, job_ids: list[str], kinds: list[str]) -> list[str]:
    """The jobs and signals a proposal's evidence names that the run handed it."""
    named = []
    for token in re.findall(r"\b[0-9a-f]{8,32}\b", evidence):
        named += [j for j in job_ids if j.startswith(token) and j not in named]
    named += [k for k in kinds if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", evidence)
              and k not in named]
    return named


def parse(text: str, job_ids: list[str], kinds: list[str]) -> tuple[list[dict], list[str]]:
    """Staged proposals and the reasons for each one dropped. A proposal that
    cannot be traced to a job or a signal is dropped, never repaired."""
    staged, dropped = [], []
    for title, body in split(str(text or "")):
        given, prose = fields_of(body)
        record = {"title": title, **{k: given.get(k, "").strip("` ") for k in FIELDS},
                  "body": prose, "spec": ""}
        record["lane"], record["action"] = record["lane"].lower(), record["action"].lower()
        problems = []
        if record["lane"] not in LANES:
            problems.append(f"lane must be one of {', '.join(LANES)}")
        if record["action"] not in ACTIONS:
            problems.append(f"action must be one of {', '.join(ACTIONS)}")
        for key in ("kind", "target", "why"):
            if not record[key]:
                problems.append(f"no {key}")
        record["traced"] = traced(record["evidence"], job_ids, kinds)
        if not record["traced"]:
            problems.append("no evidence that names a job or a signal from this run")
        if problems:
            dropped.append(f"{title}: {'; '.join(problems)}")
        else:
            staged.append(record)
    return staged, dropped


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-") or "proposal"


def unique(path: Path, run_id: str) -> Path:
    # Another proposal's spec may already have been submitted under this name.
    path.parent.mkdir(parents=True, exist_ok=True)
    n, candidate = 1, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{run_id}{'' if n == 1 else f'-{n}'}.md")
        n += 1
    return candidate


def as_spec(record: dict) -> str:
    return f"""# {record['title']}

## What

{record['action'].capitalize()} `{record['target']}` ({record['lane']}, {record['kind']}).

{record.get('body') or ''}

## Why

{record['why']}

## Where this came from

The improve lane, from: {record['evidence']}. Nothing was applied. This spec
goes through the same stage graph, delivery gate, and pull request as any other.

## Acceptance criteria

- `{record['target']}` makes the change described above.
- The reason is written where the next reader of `{record['target']}` will find it.
"""


# ------------------------------------------------------------- the runs

def folder(root: Path) -> Path:
    return root / "state" / "proposals"


def save(root: Path, run: dict) -> None:
    folder(root).mkdir(parents=True, exist_ok=True)
    path = folder(root) / f"{run['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(run, indent=2, sort_keys=True))
    tmp.replace(path)                    # a reader never sees half a run


def read(root: Path, run_id) -> dict | None:
    # A run id arrives from a person's payload, so it never names a path of its own.
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        return None
    try:
        return json.loads((folder(root) / f"{run_id}.json").read_text())
    except FileNotFoundError:
        return None


def runs(root: Path) -> list[dict]:
    if not folder(root).is_dir():
        return []
    found = [read(root, p.stem) for p in folder(root).glob("*.json")]
    return sorted((r for r in found if r), key=lambda r: r["started"], reverse=True)


def ask(call, function_id: str, payload: dict):
    """One read of another worker, or None when it does not answer."""
    try:
        answer = call(function_id, payload)
    except Exception as exc:
        print(f"improve: {function_id} did not answer: {type(exc).__name__}: {exc}", flush=True)
        return None
    return answer.get("payload", answer) if isinstance(answer, dict) else None


def start(call, root: Path, repo: str, jobs: list[dict]) -> dict:
    """factory::improve: read the record, and send one turn only if it holds trouble."""
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("factory::improve takes {\"repo\": ...}")
    path = Path(repo.strip())
    repo = str(path if path.is_absolute() else (root / path).resolve())
    if not Path(repo).is_dir():
        raise ValueError(f"repo {repo} is not a directory")
    log = ask(call, "audit::read", {})
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        # An unread record and a clean one would both show zero refusals.
        raise RuntimeError("audit::read did not answer, so the record is unknown. "
                           "Start the audit worker and run this again.")
    listed = ask(call, "ladder::list", {"repo": repo})
    missing = [] if isinstance(listed, dict) else [f"ladder::list did not answer for {repo}"]
    rules = list((listed or {}).get("rules") or [])
    signals = trouble(jobs, log["entries"], rules)
    if not signals:
        # A turn handed no evidence produces proposals anyway.
        return {"skipped": "nothing on record cost anything", "jobs": len(jobs),
                "entries": len(log["entries"]), "missing": missing}
    if not actions.ladder_registered(call):
        raise RuntimeError(f"{actions.GATE} is not registered, so no rule would refuse a call. "
                           "Start the ladder first.")
    run_id = uuid.uuid4().hex[:8]
    session = f"s_{run_id}_{PHASE}_1"
    send = turn.send_options(PHASE, repo)
    built = charter.for_repo(Path(repo))
    if built.pieces:
        send["options"]["system_prompt"] = built.text()
    deny = send["options"]["functions"].setdefault("deny", [])
    deny += [fid for fid in (listed or {}).get("withheld") or [] if fid not in deny]
    level, mode = turn.oversight(None)
    send["options"]["metadata"] |= {"job_id": run_id, "oversight": level}
    brief = as_brief(signals, jobs)
    message = actions.render((actions.prompts() / f"{PHASE}.md").read_text(),
                             {"evidence": brief, "repo": repo, "home": str(root)})
    save(root, {"id": run_id, "started": time.time(), "repo": repo, "session_id": session,
                "state": "running", "evidence": brief, "missing": missing,
                "signals": sorted({s["kind"] for s in signals}),
                "jobs": sorted({j for s in signals for j in s["jobs"]}),
                "proposals": [], "dropped": []})
    call("approval::set-mode", {"session_id": session, "mode": mode})
    call("harness::send", {"session_id": session, "idempotency_key": session,
                           "message": message, **send})
    return {"run_id": run_id, "session_id": session, "signals": len(signals),
            "missing": missing}


def completed(root: Path, event: dict) -> dict:
    """An improve turn ended: stage what traces to evidence, drop the rest, and
    keep the reasons. {} for a session that is not an improve run."""
    match = SESSION.match(str(event.get("session_id") or ""))
    run = read(root, match.group(1)) if match else None
    if run is None:
        return {}
    if run["state"] != "running":
        return {"skipped": "a stale completion"}
    if event.get("status") != "completed":
        run |= {"state": "failed", "dropped": [str(event.get("reason") or event.get("status"))]}
    else:
        staged, dropped = parse(contracts.text_of(event.get("result")),
                                run["jobs"], run["signals"])
        run |= {"state": "staged", "proposals": staged, "dropped": dropped}
    run["finished"] = time.time()
    save(root, run)
    return {"run_id": run["id"], "state": run["state"], "staged": len(run["proposals"]),
            "dropped": len(run["dropped"])}


def listed(root: Path, run_id=None) -> dict:
    """factory::proposals: every run, newest first, or one run."""
    chosen = [read(root, run_id)] if run_id else runs(root)
    return {"runs": [{"run_id": r["id"], "state": r["state"], "repo": r["repo"],
                      "proposals": [{"index": i, "title": p["title"], "lane": p["lane"],
                                     "action": p["action"], "target": p["target"],
                                     "evidence": p["evidence"], "spec": p["spec"]}
                                    for i, p in enumerate(r["proposals"])],
                      "dropped": r["dropped"]} for r in chosen if r]}


def accept(root: Path, run_id: str, index: int) -> dict:
    """factory::accept: one staged proposal becomes specs/<slug>.md, and nothing else."""
    run = read(root, run_id)
    if run is None:
        raise ValueError(f"no improve run {run_id!r}")
    staged = run["proposals"]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(staged):
        raise ValueError(f"run {run_id} has {len(staged)} proposal(s), so no index {index!r}")
    record = staged[index]
    if record.get("spec"):
        return {"already": record["spec"]}   # one decision, one spec
    path = unique(root / "specs" / f"{slug(record['title'])}.md", run_id)
    path.write_text(as_spec(record))
    record["spec"] = str(path)
    save(root, run)                          # under state/, which git ignores
    return {"spec": str(path), "next": "factory::submit"}


# ------------------------------------------------------------- the wiring

def unwired(functions: list[dict], bound: list[dict], called: set[str],
            mine: tuple = ("ladder::", "factory::", "audit::")) -> list[str]:
    reached = {t["function_id"] for t in bound} | called
    return sorted(f["function_id"] for f in functions
                  if f["function_id"].startswith(mine) and f["function_id"] not in reached)


ID = r"[a-z][\w-]*::[\w:.-]*\w"
CALLS = [re.compile(rf"""function_id["']?\s*[:=]\s*["']({ID})["']"""),
         re.compile(rf"""\b(?:call|invoke|ask|ask_engine|trigger)\(\s*(?:\w+\s*,\s*)?["']({ID})["']"""),
         re.compile(rf"""\biii["',\s]+trigger["',\s]+({ID})""")]
REGISTERS = re.compile(r"\b(?:register_trigger|registerTrigger)\(")
NAMES = re.compile(rf"""\b(?:register_function|registerFunction)\(\s*["']{ID}["']""")


def without_registrations(text: str) -> str:
    """Source with every registration cut out. Registering a function is not
    calling it, and a binding counts only when the engine reports it."""
    text = NAMES.sub("", text)
    out, at = [], 0
    for match in REGISTERS.finditer(text):
        if match.start() < at:
            continue
        out.append(text[at:match.start()])
        depth, i, quote = 0, match.end() - 1, ""
        while i < len(text):
            ch = text[i]
            if quote:
                quote = "" if ch == quote and text[i - 1] != "\\" else quote
            elif ch in "\"'`":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        at = i + 1
    return "".join(out) + text[at:]


def sources(root: Path) -> list[Path]:
    found = []
    for place, globs in (("workers", ("*.py", "*.ts")), ("scripts", ("*.py", "*.sh"))):
        for pattern in globs:
            found += [p for p in (root / place).rglob(pattern)
                      if "node_modules" not in p.parts and not p.name.endswith(".test.ts")]
    return sorted(found)


def called_ids(root: Path) -> set[str]:
    """Function ids something in the repo calls: workers/ and scripts/ (never
    tests/, because a test is not a caller), the evaluators in evals/, and OPERATOR."""
    called = set(OPERATOR)
    for path in sources(root):
        text = without_registrations(path.read_text())
        for pattern in CALLS:
            called |= set(pattern.findall(text))
    for case in sorted((root / "evals").glob("*.json")):
        try:
            called.add(str(json.loads(case.read_text())["evaluator"]["function_id"]))
        except (ValueError, KeyError, TypeError):
            continue
    return called


def rows(answer, *keys: str) -> list[dict]:
    if isinstance(answer, list):
        return [r for r in answer if isinstance(r, dict)]
    if not isinstance(answer, dict):
        return []
    for key in keys:
        if isinstance(answer.get(key), list):
            return rows(answer[key])
    return []


def carried(rules: list[dict]) -> list[str]:
    """A rule whose `rung` names a place nothing carries."""
    return [note for r in rules for note in (r.get("refused") or []) if "rung" in note]


def report(functions: list[dict], bound: list[dict], called: set[str],
           rules: list[dict]) -> dict:
    problems = []
    if not functions:
        problems.append("engine::functions::list returned nothing, so nothing was checked")
    if actions.GATE not in {f.get("function_id") for f in functions}:
        # A dead ladder has no binding, so the harness worker calls no hook and every call runs.
        problems.append(f"{actions.GATE} is not registered")
    problems += carried(rules)
    names = unwired(functions, bound, called)
    return {"ok": not names and not problems, "unwired": names, "problems": problems,
            "functions": len(functions), "bound": len(bound)}


def wiring(call, root: Path, repo: str = "") -> dict:
    """factory::wiring: every function the three workers register is bound or called."""
    functions = [f for f in rows(ask(call, "engine::functions::list", {}), "functions")
                 if "function_id" in f]
    bound = [t for t in rows(ask(call, "engine::registered-triggers::list", {}),
                             "registered_triggers", "triggers") if "function_id" in t]
    rules = rows(ask(call, "ladder::list", {"repo": repo}), "rules")
    return report(functions, bound, called_ids(root), rules)
