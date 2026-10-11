"""Chapter 9's prepare stage, the claim, teardown, settings/repos.toml, and the
Run it walk with every engine call stood in for."""

import tomllib
from pathlib import Path

import pytest
import yaml

import actions
import jobs
import repos
from conftest import Shell, git

ROOT = Path(__file__).resolve().parents[1]
SPEC = "specs/document-the-ports.md"
ANSWERED = {"exit_code": 0, "stdout": "", "stderr": ""}


def job_at(repo, stage="prepare", **extra):
    job = jobs.new(SPEC, str(repo), stage)
    return {**job, "title": "# Document the ports",
            "settings": {**repos.DEFAULTS, **extra.pop("settings", {})}, **extra}


@pytest.fixture
def made(worker, tmp_path):
    """worktree::create answers with a tree under tmp_path, and shell::exec succeeds."""
    tree = tmp_path / "tree"
    tree.mkdir()
    worker.answers |= {
        "worktree::create": lambda p: {"worktree_id": "wt_1", "path": str(tree),
                                       "branch": p["branch"]},
        "worktree::claim": {"claimed": True},
        "shell::exec": ANSWERED,
    }
    return tree


def calls(worker, function_id):
    return [p for fid, p in worker.calls if fid == function_id]


# Prepare

def test_the_branch_is_the_prefix_the_slug_and_eight_characters_of_job_id():
    """Without the id8, the second run of any spec fails with W120: branch already exists."""
    job = job_at("/r")
    assert actions.branch_name(job, {"branch_prefix": "factory/"}) == (
        f"factory/document-the-ports-{job['id'][:8]}")
    assert actions.branch_name(job, {"branch_prefix": "feature/"}).startswith("feature/")


def test_prepare_creates_writes_the_record_then_claims(worker, made, tmp_path, monkeypatch):
    """A record written after the claim would let a redelivery create a second tree (W120)."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path / "jobs"))
    job = job_at("/code/scratch")
    order = []
    worker.answers["worktree::claim"] = lambda p: order.append(jobs.read(job["id"])) or {}
    got = actions.prepare(worker, job, job["settings"])
    [create] = calls(worker, "worktree::create")
    assert create == {"repo_path": "/code/scratch", "session_id": job["id"],
                      "branch": f"factory/document-the-ports-{job['id'][:8]}", "base_ref": "main"}
    assert order[0]["worktree_id"] == "wt_1" and order[0]["path"] == str(made)
    assert calls(worker, "worktree::claim") == [{"worktree_id": "wt_1", "session_id": job["id"]}]
    assert got == {"ok": True, "record": {"worktree_id": "wt_1", "path": str(made),
                                          "branch": create["branch"]}}


def test_a_redelivered_prepare_claims_its_own_tree_again(worker, made):
    """A second worktree::create for the same branch fails with W120 and strands the job."""
    job = job_at("/r", worktree_id="wt_1", path=str(made), branch="factory/x-1")
    assert actions.prepare(worker, job, job["settings"])["ok"] is True
    assert calls(worker, "worktree::create") == []
    assert calls(worker, "worktree::claim") == [{"worktree_id": "wt_1", "session_id": job["id"]}]


def test_a_claim_another_session_holds_fails_the_stage(worker, made):
    """Two jobs in one tree commit each other's half-finished work."""
    def held(payload):
        raise RuntimeError("W210: worktree wt_1 is claimed by session s_other")
    worker.answers["worktree::claim"] = held
    job = job_at("/r", worktree_id="wt_1", path=str(made), branch="factory/x-1")
    got = actions.prepare(worker, job, job["settings"])
    assert got["ok"] is False and "W210" in got["error"]


def test_the_repos_prepare_runs_with_sh_in_the_new_tree(worker, made):
    """Without it the turn writes code against a tree with no dependencies."""
    job = job_at("/r", settings={"prepare": "./bin/setup && make db"})
    actions.prepare(worker, job, job["settings"])
    assert calls(worker, "shell::exec") == [{"command": "sh", "args": ["-c", "./bin/setup && make db"],
                                             "cwd": str(made)}]


def test_a_failed_prepare_fails_the_job_before_any_turn(worker, made):
    """Learning the stack is broken from the hook costs a paid turn; learning it here costs none."""
    worker.answers["shell::exec"] = {"exit_code": 2, "stdout": "", "stderr": "db: refused\n"}
    job = job_at("/r", settings={"prepare": "./bin/setup"})
    got = actions.prepare(worker, job, job["settings"])
    assert got["ok"] is False and "db: refused" in got["error"]
    assert got["record"]["worktree_id"] == "wt_1"     # so teardown can release it


def test_a_repo_at_its_limit_fails_the_new_job_and_names_the_holder(worker, made, tmp_path,
                                                                   monkeypatch):
    """Two prepares allocating one port fight; a reason that names no holder cannot be acted on."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path / "jobs"))
    holding = job_at("/r", stage="run", worktree_id="wt_0")
    jobs.write(holding)
    jobs.write(job_at("/r", stage="failed", worktree_id="wt_9"))    # terminal: not live
    job = job_at("/r", settings={"prepare": "./bin/setup"})
    got = actions.prepare(worker, job, job["settings"])
    assert got["ok"] is False
    assert f"job {holding['id'][:8]} at run" in got["error"] and "concurrency" in got["error"]
    assert calls(worker, "worktree::create") == []


def test_a_repo_with_no_prepare_is_not_counted(worker, made, tmp_path, monkeypatch):
    """With nothing allocated, a limit would only cost throughput."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path / "jobs"))
    jobs.write(job_at("/r", stage="run", worktree_id="wt_0"))
    job = job_at("/r")
    assert actions.prepare(worker, job, job["settings"])["ok"] is True


# Only a terminal stage releases the claim

@pytest.mark.parametrize("stage, expected", [
    ("failed", ["worktree::release"]),
    ("landed", ["worktree::release", "worktree::remove"]),
    ("closed", ["worktree::release", "worktree::remove"]),
])
def test_teardown_releases_and_removes_only_what_ended(worker, stage, expected):
    """A failed job keeps its tree, because a person wants to see what the turn left behind."""
    job = job_at("/r", stage=stage, worktree_id="wt_1")
    actions.teardown(worker, job)
    assert [fid for fid, _ in worker.calls] == expected
    assert worker.calls[0][1] == {"worktree_id": "wt_1", "session_id": job["id"]}
    if "worktree::remove" in expected:
        # A squash merge leaves the branch outside the base's ancestry, and remove refuses (W221).
        assert worker.calls[1][1] == {"worktree_id": "wt_1", "force": True}


def test_teardown_with_no_tree_calls_nothing(worker):
    """A job that failed before prepare made a tree has nothing to release."""
    assert actions.teardown(worker, job_at("/r", stage="failed")) == {"did": []}
    assert worker.calls == []


def test_a_failed_release_does_not_stop_teardown(worker):
    """The job already ended; tidying that fails must not reopen a person's decision."""
    def gone(payload):
        raise RuntimeError("W404: no such worktree")
    worker.answers["worktree::release"] = gone
    got = actions.teardown(worker, job_at("/r", stage="landed", worktree_id="wt_1"))
    assert got["did"][0].startswith("release failed") and got["did"][1] == "removed"


@pytest.fixture
def walked(factory_main, settings_dir, git_repo):
    """settings/ for a full walk: plan and run phases, and the git_repo as a local forge."""
    (settings_dir / "phases.yaml").write_text("phases:\n  plan: {}\n  run: {}\n")
    (settings_dir / "repos.toml").write_text(
        f'[repos."{git_repo}"]\nforge = "local"\nbase = "main"\n')
    shell = Shell()

    def create_file(payload):
        for f in payload["files"]:
            Path(f["path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(f["path"]).write_text(f["content"])
        return {"results": [{"path": f["path"], "success": True} for f in payload["files"]]}

    def read_file(payload):
        return {"content": Path(payload["path"]).read_text()}

    factory_main.worker.answers |= {
        "worktree::create": {"worktree_id": "wt_1", "path": str(git_repo)},
        "worktree::claim": {}, "worktree::release": {}, "worktree::remove": {},
        "shell::exec": shell, "ladder::evaluate": {"decision": "continue"},
        "coder::create-file": create_file, "coder::read-file": read_file,
    }
    return factory_main


def deliver(factory_main, job_id):
    """Drain the queue; finish each turn as the harness would, after `edits` runs."""
    done = 0
    while True:
        messages = [p["data"] for fid, p in factory_main.worker.calls
                    if fid == "iii::durable::publish"]
        if done == len(messages):
            return jobs.read(job_id)
        factory_main.step(messages[done])
        done += 1
        job = jobs.read(job_id)
        if job.get("session_id"):
            yield job
            factory_main.turn_completed({"session_id": job["session_id"], "status": "completed",
                                         "terminal": True})


def hook(repo, body):
    path = repo / ".git" / "hooks" / "pre-commit"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def sent(factory_main):
    return [p for fid, p in factory_main.worker.calls if fid == "harness::send"]


def test_run_it_a_refused_commit_revises_once_and_opens_one_request(walked, git_repo):
    """The chapter's Run it, with every paid turn and engine call stood in for."""
    hook(git_repo, 'git diff --cached --name-only | grep -q CHANGELOG.md || '
                   '{ echo "CHANGELOG.md is unchanged. Add a line for this change."; exit 1; }')
    job_id = walked.submit({"spec": SPEC, "repo": str(git_repo)})["job_id"]
    for job in deliver(walked, job_id):
        if job["session_id"].endswith("_run_1"):
            (git_repo / "README.md").write_text("# target\n\n## Ports\n\nNone.\n")
        if job["session_id"].endswith("_run_2"):
            (git_repo / "CHANGELOG.md").write_text("- Document the ports\n")
    job = jobs.read(job_id)
    assert (job["stage"], job["revisions"]) == ("waiting", 1)
    run_2 = next(s for s in sent(walked) if s["session_id"] == f"s_{job_id[:8]}_run_2")
    assert run_2["message"] == "CHANGELOG.md is unchanged. Add a line for this change.\n"
    [request] = list((git_repo / "requests").iterdir())
    assert request.name == f"factory-document-the-ports-{job_id[:8]}.md"
    assert job["branch"] == f"factory/document-the-ports-{job_id[:8]}"
    assert git(git_repo, "log", "-1", "--format=%s").strip() == "Document the ports"
    assert [fid for fid, _ in walked.worker.calls if fid.startswith("worktree::")] == [
        "worktree::create", "worktree::claim"]           # waiting still holds the claim


def test_run_it_an_identical_refusal_fails_after_one_revision(walked, git_repo):
    """The break: an unconditional hook, so the second refusal is the same bytes as the first."""
    hook(git_repo, 'echo "frozen until Monday"; exit 1')
    job_id = walked.submit({"spec": SPEC, "repo": str(git_repo)})["job_id"]
    for job in deliver(walked, job_id):
        (git_repo / f"{job['session_id']}.txt").write_text("work\n")
    job = jobs.read(job_id)
    assert (job["stage"], job["revisions"]) == ("failed", 1)
    assert job["history"][-1]["why"].startswith("the refusal came back word for word")
    assert [s["session_id"].split("_", 2)[2] for s in sent(walked)] == ["plan_1", "run_1", "run_2"]
    assert not (git_repo / "requests").exists()
    assert ("worktree::release", {"worktree_id": "wt_1", "session_id": job_id}) in walked.worker.calls
    assert not any(fid == "worktree::remove" for fid, _ in walked.worker.calls)


# Settings and wiring

def test_submit_copies_the_resolved_repo_settings_onto_the_job(factory_main, settings_dir, git_repo):
    """A rework weeks later must rebuild the job's original environment, not today's file."""
    (settings_dir / "repos.toml").write_text(
        f'[defaults]\nbranch_prefix = "bot/"\n[repos."{git_repo}"]\nprepare = "./bin/setup"\n')
    job = jobs.read(factory_main.submit({"spec": SPEC, "repo": str(git_repo)})["job_id"])
    assert job["settings"] == {**repos.DEFAULTS, "branch_prefix": "bot/", "prepare": "./bin/setup"}
    assert job["title"] == "# Document the ports" and job["stage"] == "prepare"
    (settings_dir / "repos.toml").write_text("")
    assert jobs.read(job["id"])["settings"]["prepare"] == "./bin/setup"


@pytest.mark.parametrize("table, said", [
    ({"forge": "gitlab"}, "gitlab"),
    ({"forge": "github"}, "slug"),
])
def test_a_repo_table_the_factory_cannot_deliver_through_is_refused(table, said):
    """A job with no driver fails after its paid turns, at publish."""
    with pytest.raises(ValueError, match=said):
        repos.resolve("/r", {"repos": {"/r": table}})


def test_the_shipped_repos_file_has_the_defaults_and_the_scratch_table():
    """Every repository gets its own table, and the prefix is the factory's convention."""
    shipped = tomllib.loads((ROOT / "settings" / "repos.toml").read_text())
    assert shipped["defaults"] == {"branch_prefix": "factory/"}
    assert shipped["repos"]["/Users/you/code/scratch"] == {
        "forge": "local", "base": "main", "prepare": "./bin/setup"}
    assert repos.resolve("/Users/you/code/scratch", shipped)["concurrency"] == 1


def test_compose_pins_worktree_and_github_and_landing_is_off():
    """A worktree worker that can land is a merge with no person in it."""
    compose = yaml.safe_load((ROOT / "worker-compose.yaml").read_text())["containers"]
    assert (compose["worktree"]["worker"], compose["worktree"]["version"]) == (
        "package://worktree", "0.3.3")
    assert (compose["github"]["worker"], compose["github"]["version"]) == (
        "package://github", "0.3.2")
    assert compose["github"]["env_file"] == ["./.env"]
    gates = yaml.safe_load((ROOT / "config" / "worktree.yaml").read_text())["value"]["gates"]
    assert gates["allow_land"] is False
    assert yaml.safe_load((ROOT / "config" / "github.yaml").read_text())["value"]["token"] == ""
    assert "GH_TOKEN=\n" in (ROOT / ".env.example").read_text()


def test_the_factory_never_calls_land():
    """allow_land: false refuses others; the factory must not need the refusal."""
    for source in (ROOT / "workers" / "factory" / "src").glob("*.py"):
        assert "worktree::land" not in source.read_text()


def test_the_shipped_pipeline_starts_at_prepare_with_a_bounded_commit():
    """A commit loop with no identical-refusal stop pays a turn on every job a fixed hook refuses."""
    shipped = yaml.safe_load((ROOT / "settings" / "pipeline.yaml").read_text())
    assert shipped["first"] == "prepare"
    assert shipped["stages"]["prepare"] == {"action": "prepare", "next": "plan"}
    assert shipped["stages"]["commit"] == {"action": "commit", "next": "publish", "on_refusal": {
        "goto": "run", "max": 2, "stop_when_identical": True}}
