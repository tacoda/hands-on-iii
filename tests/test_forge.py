"""Chapter 9's forge drivers and the publish stage. Every driver is pure: it
returns calls and reads answers, so these tests need no forge."""

import pytest

import actions
import forge
import repos

JOB_ID = "0123abcd" + "0" * 24
BRANCH = "factory/document-the-ports-0123abcd"


def job_for(name="local", **extra):
    return {"id": JOB_ID, "repo": "/code/scratch", "branch": BRANCH,
            "title": "# Document the ports", "spec": "specs/document-the-ports.md",
            "settings": {**repos.DEFAULTS, "forge": name, "slug": "you/repo"}, **extra}


def ok(value):
    return {"ok": True, "value": value}


# The local forge: a request is a file

def test_local_open_writes_one_request_file_named_for_the_branch():
    """A slash in the branch would put the request in a directory nobody looks in."""
    [one] = forge.local_open(job_for(), "the body\n")
    assert one["function_id"] == "coder::create-file"
    [file] = one["payload"]["files"]
    assert file["path"] == "/code/scratch/requests/factory-document-the-ports-0123abcd.md"
    assert (file["overwrite"], file["parents"]) == (True, True)


def test_the_request_file_says_its_status_branch_and_how_to_land_or_reject_it():
    """A request a person cannot act on without reading the factory's code is not a request."""
    text = forge.request_text(job_for(), "the body\n")
    assert text.startswith("# Document the ports\n\nstatus: open\n")
    assert f"branch: {BRANCH}\n" in text and "base: main\n" in text
    assert f"merge `{BRANCH}` into `main`" in text and "`status: closed`" in text
    assert text.endswith("the body\n")
    assert forge.status_of(text) == "open"


@pytest.mark.parametrize("text, status", [
    ("status: open\n", "open"),
    ("# t\n\nstatus: closed\nbranch: b\n", "closed"),
    ("Status:   Closed  \n", "closed"),
    ("no status line here\n", "open"),
    ("the old status: closed is prose\nstatus: open\n", "open"),
    ("", "open"),
])
def test_status_is_read_from_its_own_line(text, status):
    """A status read from prose would close a request because its body mentioned closing."""
    assert forge.status_of(text) == status


def test_local_state_reads_the_file_and_the_merge(tmp_path):
    """Merging the branch is how a person lands a local request, so ancestry is the merge."""
    job = job_for()
    calls = forge.local_view(job)
    assert [c["function_id"] for c in calls] == ["coder::read-file", "shell::exec"]
    assert calls[1]["payload"]["args"] == ["merge-base", "--is-ancestor", BRANCH, "main"]
    merged = forge.local_state(job, [ok({"content": "status: open\n"}), ok({"exit_code": 0})])
    assert merged == {"state": "open", "merged": True}
    closed = forge.local_state(job, [ok({"content": "status: closed\n"}), ok({"exit_code": 1})])
    assert closed == {"state": "closed", "merged": False}


def test_a_timed_out_merge_check_is_not_a_landing_and_does_not_raise():
    """A timeout has exit_code null: int() of it raised, and `or 0` would read it as merged."""
    timed_out = ok({"exit_code": None, "timed_out": True, "stdout": "", "stderr": ""})
    got = forge.local_state(job_for(), [ok({"content": "status: open\n"}), timed_out])
    assert got == {"state": "open", "merged": False}
    null = ok({"exit_code": None, "stdout": "", "stderr": ""})
    assert forge.local_state(job_for(), [ok({"content": "status: open\n"}), null])["merged"] is False


@pytest.mark.parametrize("answer", [
    ok({"results": [{"path": "/code/scratch/requests/x.md", "success": False,
                     "error": "C201: outside the jail"}]}),
    ok({"results": []}),
    ok({}),
])
def test_local_opened_checks_each_files_success(answer):
    """coder::create-file answers per file, so a call that succeeded can still have written nothing."""
    got = forge.local_opened(job_for(), [answer])
    assert set(got) == {"error"} and got["error"]


def test_local_opened_records_the_request_when_every_file_was_written():
    """The record is the request's path, and only a written file earns it."""
    answer = ok({"results": [{"path": "p", "success": True}]})
    path = forge.request_path(job_for())
    assert forge.local_opened(job_for(), [answer]) == {"id": path, "url": path}


def test_a_failed_read_never_looks_like_a_landing():
    """A forge that could not answer must leave the job waiting, not land it."""
    failed = {"ok": False, "error": "down"}
    assert forge.local_state(job_for(), [failed, failed]) == {"state": "open", "merged": False}
    assert actions.outcome_of(forge.local_state(job_for(), [failed, failed])) == ""


@pytest.mark.parametrize("state, outcome", [
    ({"state": "open", "merged": False}, ""),
    ({"state": "open", "merged": True}, "merge"),
    ({"state": "closed", "merged": False}, "close"),
    ({"state": "closed", "merged": True}, "merge"),
])
def test_outcome_of_maps_a_state_to_an_edge(state, outcome):
    """A merged pull request also reads as closed on GitHub; merge must win."""
    assert actions.outcome_of(state) == outcome


def test_local_found_needs_an_open_request_file():
    """A closed request from an earlier job is not this job's request."""
    job = job_for()
    assert forge.local_found(job, [ok({"content": "status: open\n"})])["id"].endswith(
        "factory-document-the-ports-0123abcd.md")
    assert forge.local_found(job, [ok({"content": "status: closed\n"})]) is None
    assert forge.local_found(job, [{"ok": False, "error": "no such file"}]) is None


# The github forge

def test_github_open_is_one_pr_create_with_the_branch_as_head():
    """A head that is not the branch opens a pull request for someone else's work."""
    [one] = forge.github_open(job_for("github"), "body")
    assert one == {"function_id": "github::pr::create", "allow_failure": False, "payload": {
        "repo": "you/repo", "title": "Document the ports", "body": "body",
        "head": BRANCH, "base": "main"}}


def test_github_looks_up_an_open_pull_for_the_branch_first():
    """A crash between opening and recording would otherwise open a second pull request."""
    [one] = forge.github_find(job_for("github"))
    assert one["function_id"] == "github::api"
    assert one["payload"]["path"] == f"repos/you/repo/pulls?state=open&head=you:{BRANCH}"
    pulls = ok({"data": [{"number": 7, "html_url": "https://github.com/you/repo/pull/7"}]})
    assert forge.github_found(job_for("github"), [pulls])["id"] == 7
    assert forge.github_found(job_for("github"), [ok({"data": "[]"})]) is None


def test_github_opened_reads_the_number_from_the_url():
    """Without a number the watch stage has nothing to view, and waits forever."""
    got = forge.github_opened(job_for("github"), [ok({"stdout": "https://github.com/you/repo/pull/12\n"})])
    assert got == {"id": 12, "url": "https://github.com/you/repo/pull/12"}


def test_github_reads_comments_from_both_endpoints():
    """github::pr::view returns no comments, so reading it alone hears no reviewer."""
    job = job_for("github", request={"id": 12})
    paths = [c["payload"]["path"] for c in forge.github_comment_calls(job)]
    assert paths == ["repos/you/repo/issues/12/comments", "repos/you/repo/pulls/12/comments"]
    got = forge.github_comments([ok({"data": [{"id": 2, "body": "b", "created_at": "2"}]}),
                                 ok({"data": [{"id": 1, "body": "a", "created_at": "1"}]})])
    assert [c["body"] for c in got] == ["a", "b"]


def test_github_state_reads_merged():
    """gh reports MERGED in capitals; a case-sensitive check would never land a job."""
    job = job_for("github", request={"id": 12})
    assert forge.github_state(job, [ok({"state": "MERGED"})])["merged"] is True
    assert forge.github_state(job, [ok({"state": "OPEN"})]) == {"state": "open", "merged": False}


def test_github_reads_the_answers_the_worker_actually_returns():
    """github::pr::view and github::api nest their answer under `value`, and invoke
    wraps that again. Reading only the outer layer saw every pull request as open with
    no comments, so a merge, a close, or a review never moved a job."""
    job = job_for("github", request={"id": 12})
    closed = ok({"value": {"state": "CLOSED", "mergedAt": None}})
    assert forge.github_state(job, [closed]) == {"state": "closed", "merged": False}
    said = ok({"value": [{"id": 3, "body": "c", "created_at": "3"}]})
    assert [c["body"] for c in forge.github_comments([said])] == ["c"]
    found = ok({"value": [{"number": 7, "html_url": "u"}]})
    assert forge.github_found(job, [found]) == {"id": 7, "url": "u"}


# The forge is a setting

def test_the_driver_comes_from_the_settings_on_the_job():
    """A repository that changed forge since must not change how an old job is delivered."""
    assert forge.for_job(job_for()).remote == ""
    assert forge.for_job(job_for("github")).remote == "origin"
    with pytest.raises(ValueError, match="gitlab"):
        forge.for_job(job_for("gitlab"))


def test_title_of_strips_the_heading_marker():
    """`git commit -m` keeps the `#`, and every commit then reads as a heading."""
    assert forge.title_of({"title": "#  Document the ports "}) == "Document the ports"


# Publish is idempotent

@pytest.fixture
def files(worker):
    """coder::create-file and coder::read-file against a dict, standing in for a disk."""
    disk = {}

    def create(payload):
        for f in payload["files"]:
            disk[f["path"]] = f["content"]
        return {"results": [{"path": f["path"], "success": True} for f in payload["files"]]}

    def read(payload):
        if payload["path"] not in disk:
            raise RuntimeError("no such file")
        return {"content": disk[payload["path"]]}

    worker.answers |= {"coder::create-file": create, "coder::read-file": read}
    return disk


def opened(worker, function_id):
    return [p for fid, p in worker.calls if fid == function_id]


def test_publish_opens_one_local_request_and_records_it(worker, files):
    """The record is what tells a rework there is already a request on this branch."""
    got = actions.publish(worker, job_for(), {})
    assert got["opened"] is True
    path = "/code/scratch/requests/factory-document-the-ports-0123abcd.md"
    assert got["record"] == {"request": {"id": path, "url": path}}
    assert forge.status_of(files[path]) == "open"
    assert "## Acceptance criteria" in files[path]   # the spec is the body, for now


def test_publish_finds_a_request_a_crash_left_unrecorded(worker, files):
    """A crash between opening and recording would otherwise open a second request."""
    actions.publish(worker, job_for(), {})
    worker.calls.clear()
    got = actions.publish(worker, job_for(), {})
    assert got["opened"] is False and got["record"]["request"]["id"].endswith(".md")
    assert opened(worker, "coder::create-file") == []


def test_publish_fails_when_create_file_wrote_no_request(worker):
    """Recording a request that is not on disk leaves a job waiting on a file nobody can merge."""
    worker.answers["coder::create-file"] = {"results": [
        {"path": "x.md", "success": False, "error": "C203: protected path"}]}
    got = actions.publish(worker, job_for(), {})
    assert got["ok"] is False and "C203" in got["error"] and "record" not in got


def test_publish_opens_nothing_when_the_job_already_has_a_request(worker, files):
    """A rework pushes to the same branch, and the request updates itself."""
    got = actions.publish(worker, job_for(request={"id": "x", "url": "x"}), {})
    assert got == {"ok": True, "opened": False} and worker.calls == []


def test_publish_on_github_records_an_open_pull_instead_of_opening_one(worker):
    """Two pull requests for one branch split the review in half."""
    worker.answers["github::api"] = {"data": [{"number": 7, "html_url": "u"}]}
    got = actions.publish(worker, job_for("github"), {})
    assert got["record"] == {"request": {"id": 7, "url": "u"}}
    assert opened(worker, "github::pr::create") == []


def test_publish_fails_when_it_cannot_look_for_an_open_pull(worker):
    """Opening without looking is how a second pull request appears after a crash."""
    def down(payload):
        raise RuntimeError("gh: must be a collaborator")
    worker.answers["github::api"] = down
    got = actions.publish(worker, job_for("github"), {})
    assert got["ok"] is False and "collaborator" in got["error"]
    assert opened(worker, "github::pr::create") == []


def test_watch_lands_a_merged_local_request(worker, files):
    """The person's merge is the only thing that moves a job to landed."""
    job = job_for()
    job |= actions.publish(worker, job, {})["record"]
    worker.answers["shell::exec"] = {"exit_code": 0, "stdout": "", "stderr": ""}
    assert actions.watch(worker, job, {}) == {"outcome": "merge"}
    worker.answers["shell::exec"] = {"exit_code": 1, "stdout": "", "stderr": ""}
    assert actions.watch(worker, job, {}) == {"outcome": ""}
