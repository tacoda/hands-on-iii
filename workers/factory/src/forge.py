"""Where a request for review lives. One driver per forge, all of it pure.

A driver answers four questions: how to open a request, read its state, read
the comments, and reply. It also finds an open request for a branch, so a
crash between opening one and recording it never opens a second. Each answer
is a list of calls for the factory to make, plus a function that reads what
came back, so a test checks a driver with no forge running.

`local` is built first: the request is a markdown file in the target
repository's `requests/`, landed by merging the branch and rejected by setting
its status to `closed`. `github` is the stock worker. `for_job` picks one from
the settings copied onto the job.
"""

import json
import re
from dataclasses import dataclass
from typing import Callable

STATUS = re.compile(r"^status:\s*(\w+)\s*$", re.MULTILINE | re.IGNORECASE)


def call(function_id: str, payload: dict, allow_failure: bool = False) -> dict:
    return {"function_id": function_id, "payload": payload, "allow_failure": allow_failure}


def title_of(job: dict) -> str:
    # `git commit -m "# Document the ports"` keeps the `#`, and every commit
    # in a scratch repository then reads as a heading.
    return str(job.get("title") or "").strip().lstrip("#").strip()[:72] or "factory change"


def base_of(job: dict) -> str:
    return str((job.get("settings") or {}).get("base") or "main")


def payload_of(answer: dict):
    """What the worker answered. github::pr::view and github::api nest their
    answer under their own `value`, inside the one invoke adds, so unwrap both."""
    value = answer.get("value") if isinstance(answer, dict) else None
    while isinstance(value, dict) and set(value) == {"value"}:
        value = value["value"]
    return value


def value_of(answer: dict) -> dict:
    value = payload_of(answer)
    return value if isinstance(value, dict) else {}


def text_of(answer: dict) -> str:
    value = value_of(answer)
    for key in ("content", "text", "output", "stdout"):
        if isinstance(value.get(key), str):
            return value[key]
    return ""


def as_list(answer: dict) -> list:
    """A REST endpoint's array, parsed or still JSON text."""
    value = payload_of(answer)
    if isinstance(value, dict):
        value = value.get("data", value.get("output", value.get("stdout", value)))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class Driver:
    name: str
    remote: str                                   # "" means nothing to push to
    open_calls: Callable[[dict, str], list]
    opened: Callable[[dict, list], dict]
    find_calls: Callable[[dict], list]
    found: Callable[[dict, list], dict | None]
    view_calls: Callable[[dict], list]
    state: Callable[[dict, list], dict]
    comment_calls: Callable[[dict], list]
    comments: Callable[[list], list]
    say_calls: Callable[[dict, str], list]


# ------------------------------------------------------------------- local

def request_path(job: dict) -> str:
    return f"{job['repo']}/requests/{job['branch'].replace('/', '-')}.md"


def request_text(job: dict, body: str) -> str:
    branch, base = job["branch"], base_of(job)
    return (f"# {title_of(job)}\n\n"
            f"status: open\n"
            f"branch: {branch}\n"
            f"base: {base}\n\n"
            f"To land this request, merge `{branch}` into `{base}`. To reject it, "
            f"change the status line above to `status: closed`.\n\n"
            f"{body.rstrip()}\n")


def status_of(text: str) -> str:
    """The request file's status. A file with no status line reads as open."""
    found = STATUS.search(text or "")
    return found.group(1).lower() if found else "open"


def local_open(job: dict, body: str) -> list:
    path = f"{job['repo']}/requests/{job['branch'].replace('/', '-')}.md"
    text = request_text(job, body)  # status: open, the branch, how to land or reject
    return [call("coder::create-file", {"files": [
        {"path": path, "content": text, "overwrite": True, "parents": True}]})]


def local_request(job: dict) -> dict:
    return {"id": request_path(job), "url": request_path(job)}


def unwritten(answer: dict) -> str:
    """Why coder::create-file wrote nothing, or "".

    It answers per file in `results[].success`, so a call that succeeded can
    still have written no file. No per-file results is a failure too.
    """
    results = value_of(answer).get("results")
    if not isinstance(results, list) or not results:
        return "coder::create-file reported no per-file results"
    failed = [r for r in results if not (isinstance(r, dict) and r.get("success") is True)]
    return "; ".join(f"{r.get('path', '?')}: {r.get('error') or 'not written'}"
                     if isinstance(r, dict) else str(r) for r in failed)


def local_opened(job: dict, answers: list) -> dict:
    why = unwritten(answers[0]) if answers else "coder::create-file did not answer"
    return {"error": why} if why else local_request(job)


def local_find(job: dict) -> list:
    # A missing file is the usual answer, not a failure.
    return [call("coder::read-file", {"path": request_path(job)}, allow_failure=True)]


def local_found(job: dict, answers: list) -> dict | None:
    if not answers or not answers[0].get("ok"):
        return None
    text = text_of(answers[0])
    return local_request(job) if text and status_of(text) == "open" else None


def local_view(job: dict) -> list:
    return [call("coder::read-file", {"path": request_path(job)}, allow_failure=True),
            # Exit 0 once somebody merged the branch into the base.
            call("shell::exec", {"command": "git",
                                 "args": ["merge-base", "--is-ancestor", job["branch"],
                                          base_of(job)],
                                 "cwd": job["repo"]}, allow_failure=True)]


def local_state(job: dict, answers: list) -> dict:
    text = text_of(answers[0]) if answers and answers[0].get("ok") else ""
    merged = value_of(answers[1]) if len(answers) > 1 and answers[1].get("ok") else {}
    # A failed read must never look like a landing, and a timeout's exit_code
    # is null, so it is compared, never int()ed.
    return {"state": status_of(text) if text else "open",
            "merged": not merged.get("timed_out") and merged.get("exit_code") == 0}


def local_comment_calls(job: dict) -> list:
    return [call("coder::read-file", {"path": request_path(job)}, allow_failure=True)]


def local_comments(answers: list) -> list:
    return []  # chapter 10 reads the comments in the request file


def local_say(job: dict, body: str) -> list:
    return []  # a file has no one to notify, so saying nothing is honest


LOCAL = Driver(name="local", remote="", open_calls=local_open, opened=local_opened,
               find_calls=local_find, found=local_found, view_calls=local_view,
               state=local_state, comment_calls=local_comment_calls,
               comments=local_comments, say_calls=local_say)


# ------------------------------------------------------------------ github

def slug_of(job: dict) -> str:
    return str((job.get("settings") or {}).get("slug") or "")


def github_open(job: dict, body: str) -> list:
    return [call("github::pr::create", {"repo": slug_of(job), "title": title_of(job),
                                        "body": body, "head": job["branch"],
                                        "base": base_of(job)})]


def github_opened(job: dict, answers: list) -> dict:
    value = value_of(answers[0]) if answers else {}
    url = str(value.get("url") or value.get("output") or value.get("stdout") or "").strip()
    number = value.get("number")
    if not number and url.rstrip("/").rsplit("/", 1)[-1].isdigit():
        number = int(url.rstrip("/").rsplit("/", 1)[-1])  # gh prints the URL
    return {"id": number, "url": url}


def github_find(job: dict) -> list:
    owner = slug_of(job).split("/", 1)[0]
    return [call("github::api", {"path": f"repos/{slug_of(job)}/pulls?state=open"
                                         f"&head={owner}:{job['branch']}",
                                 "method": "GET"})]


def github_found(job: dict, answers: list) -> dict | None:
    pulls = [p for p in (as_list(answers[0]) if answers else []) if isinstance(p, dict)]
    if not pulls:
        return None
    return {"id": pulls[0].get("number"), "url": pulls[0].get("html_url") or ""}


def github_view(job: dict) -> list:
    return [call("github::pr::view", {"repo": slug_of(job),
                                      "number": (job.get("request") or {}).get("id")})]


def github_state(job: dict, answers: list) -> dict:
    value = value_of(answers[0]) if answers and answers[0].get("ok") else {}
    state = str(value.get("state") or "open").lower()
    return {"state": state, "merged": state == "merged" or bool(value.get("mergedAt"))}


def github_comment_calls(job: dict) -> list:
    # github::pr::view returns no comments, and GitHub keeps the conversation
    # and the line comments on two endpoints.
    slug, number = slug_of(job), (job.get("request") or {}).get("id")
    return [call("github::api", {"path": f"repos/{slug}/{kind}/{number}/comments",
                                 "method": "GET"}, allow_failure=True)
            for kind in ("issues", "pulls")]


def github_comments(answers: list) -> list:
    found = [{"id": str(c.get("id") or ""), "body": str(c.get("body") or ""),
              "created_at": str(c.get("created_at") or "")}
             for answer in answers for c in as_list(answer) if isinstance(c, dict)]
    return sorted(found, key=lambda c: c["created_at"])


def github_say(job: dict, body: str) -> list:
    return [call("github::pr::comment", {"repo": slug_of(job), "body": body,
                                         "number": (job.get("request") or {}).get("id")})]


GITHUB = Driver(name="github", remote="origin", open_calls=github_open,
                opened=github_opened, find_calls=github_find, found=github_found,
                view_calls=github_view, state=github_state,
                comment_calls=github_comment_calls, comments=github_comments,
                say_calls=github_say)

DRIVERS = {"local": LOCAL, "github": GITHUB}


def for_job(job: dict) -> Driver:
    # From the settings copied onto the job, so a repository that changed
    # forge since never changes how an old job is delivered.
    name = str((job.get("settings") or {}).get("forge") or "local")
    if name not in DRIVERS:
        raise ValueError(f"no forge driver named {name!r}")
    return DRIVERS[name]
