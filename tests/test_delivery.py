"""Chapter 9's commit stage: rung 4, then the repository's hook, then the push.

Real git in a temporary repository; the ladder, the remote, and every engine
call are stand-ins, so nothing here needs an engine or a network.
"""

import pytest

import actions
import jobs
import repos
from conftest import Shell, git
from graph import DEFAULT, next_stage

JOB_ID = "0123abcd" + "0" * 24
BRANCH = "factory/document-the-ports-0123abcd"
HOOK = "#!/bin/sh\necho 'CHANGELOG.md is unchanged.'\necho 'Add a line under Unreleased.' >&2\nexit 1\n"


def job_for(repo, forge="local", **extra):
    if forge == "github":
        git(repo, "update-ref", "refs/remotes/origin/main", "main")  # as a fetch would leave it
    return {"id": JOB_ID, "repo": str(repo), "path": str(repo), "branch": BRANCH,
            "title": "# Document the ports", "spec": "specs/document-the-ports.md",
            "stage": "commit", "settings": {**repos.DEFAULTS, "forge": forge,
                                            "slug": "you/repo"}, **extra}


@pytest.fixture
def shell(worker):
    worker.answers["shell::exec"] = Shell()
    worker.answers["ladder::evaluate"] = lambda p: {"decision": "continue"}
    return worker.answers["shell::exec"]


def evaluated(worker):
    return [p for fid, p in worker.calls if fid == "ladder::evaluate"]


def commits(repo):
    return git(repo, "rev-list", "--count", "main..HEAD").strip()


def hook(repo, text=HOOK):
    path = repo / ".git" / "hooks" / "pre-commit"
    path.write_text(text)
    path.chmod(0o755)


# shell::exec reports a failure in its payload

def test_a_nonzero_exit_code_is_a_failed_run(worker):
    """shell::exec does not raise, so a hook refusing a commit would read as a commit that worked."""
    worker.answers["shell::exec"] = lambda p: {"exit_code": 1, "stdout": "no\n", "stderr": "why\n"}
    got = actions.run(worker, "git", ["commit", "-m", "x"], "/tmp")
    assert got == {"ok": False, "output": "no\nwhy\n", "error": "no\nwhy\n"}


def test_a_timed_out_command_is_a_failed_run(worker):
    """A timeout has exit_code null, and `or 0` read it as a command that worked."""
    worker.answers["shell::exec"] = lambda p: {"exit_code": None, "timed_out": True,
                                                "stdout": "half\n", "stderr": ""}
    got = actions.run(worker, "sh", ["-c", "./bin/setup"], "/w")
    assert got["ok"] is False and got["output"] == "half\n"


def test_a_null_exit_code_is_a_failed_run(worker):
    """No exit code means nobody saw the command succeed."""
    worker.answers["shell::exec"] = lambda p: {"exit_code": None, "stdout": "", "stderr": ""}
    assert actions.run(worker, "git", ["status"], "/w")["ok"] is False


def test_run_sends_a_program_and_an_argument_list(worker):
    """`git add -A && git commit` as one command spawns nothing (S216) and pushes an empty branch."""
    worker.answers["shell::exec"] = lambda p: {"exit_code": 0, "stdout": "ok", "stderr": ""}
    assert actions.run(worker, "git", ["add", "-A"], "/w") == {"ok": True, "output": "ok"}
    assert worker.calls[-1] == ("shell::exec", {"command": "git", "args": ["add", "-A"],
                                                "cwd": "/w"})


# Rung 4: only an empty pair commits

@pytest.mark.parametrize("answer, kind", [
    (lambda p: {"decision": "deny", "reason": "a key in leak.py line 1"}, "refused"),
    (lambda p: (_ for _ in ()).throw(RuntimeError("function_not_found")), "problem"),
    (lambda p: None, "problem"),
    (lambda p: {}, "problem"),
])
def test_only_an_empty_refusal_and_no_problem_reaches_git_commit(worker, shell, git_repo,
                                                                 answer, kind):
    """A ladder that cannot answer returns an empty refusal, which reads exactly like a clean change."""
    (git_repo / "leak.py").write_text("x = 1\n")
    worker.answers["ladder::evaluate"] = answer
    got = actions.commit(worker, job_for(git_repo), {})
    assert commits(git_repo) == "0"
    assert not any(args[:2] == ["git", "commit"] for args in shell.ran)
    if kind == "refused":
        assert got == {"ok": True, "refused": True, "refusal": "a key in leak.py line 1"}
    else:
        assert got["ok"] is False and "ladder" in got["error"]


def test_a_clean_gate_commits_with_the_title_as_the_message(worker, shell, git_repo):
    """A `#` kept from the spec's heading makes every commit read as a heading."""
    (git_repo / "README.md").write_text("# target\n\n## Ports\n")
    got = actions.commit(worker, job_for(git_repo), {})
    assert got == {"ok": True, "committed": True}
    assert git(git_repo, "log", "-1", "--format=%s").strip() == "Document the ports"


def test_a_diff_the_gate_cannot_read_is_a_problem(worker, shell, git_repo):
    """A failed git diff with no findings would pass work nobody read."""
    (git_repo / "a.txt").write_text("a\n")
    job = job_for(git_repo)
    job["settings"]["base"] = "no-such-base"
    got = actions.commit(worker, job, {})
    assert got["ok"] is False and "could not read the change" in got["error"]
    assert evaluated(worker) == [] and commits(git_repo) == "0"


def test_the_gate_asks_once_per_file_with_its_path_and_the_message(worker, shell, git_repo):
    """An empty path asks every path-scoped rule about every file, and no finding can name one."""
    (git_repo / "a.txt").write_text("alpha\n")
    (git_repo / "b.txt").write_text("beta\n")
    actions.commit(worker, job_for(git_repo), {})
    asked = evaluated(worker)
    assert [p["path"] for p in asked] == ["a.txt", "b.txt"]
    assert all(p["rung"] == 4 and p["repo"] == str(git_repo) for p in asked)
    assert asked[0]["content"] == "alpha\nDocument the ports"   # an escape can live in the message


def test_the_gate_reads_work_the_turn_committed_itself(worker, shell, git_repo):
    """The run phase holds shell::exec, and a gate reading only the index would see an empty diff."""
    (git_repo / "leak.py").write_text("token = 1\n")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-q", "-m", "the turn did it")
    actions.commit(worker, job_for(git_repo), {})
    assert [p["path"] for p in evaluated(worker)] == ["leak.py"]


def test_an_empty_change_still_asks_about_the_outbound_text(worker, shell, git_repo):
    """No tool writes the commit message, so rung 4 is the only rung that ever sees it."""
    actions.commit(worker, job_for(git_repo), {})
    assert evaluated(worker) == [{"repo": str(git_repo), "path": "",
                                  "content": "Document the ports", "rung": 4}]


def test_per_file_keeps_only_added_lines():
    """Removing a key must not be refused for containing one."""
    diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n"
            "-old = 'sk-ant-secretsecret'\n+new = 1\n+++ still added\n")
    assert actions.per_file(diff) == [("x.py", "new = 1\n++ still added")]


# The repository's hook, verbatim

def test_a_hook_refusal_comes_back_word_for_word(worker, shell, git_repo):
    """A summary keeps the gist and drops the file name and line the next turn needs."""
    hook(git_repo)
    (git_repo / "README.md").write_text("changed\n")
    got = actions.commit(worker, job_for(git_repo), {})
    assert got == {"ok": True, "refused": True,
                   "refusal": "CHANGELOG.md is unchanged.\nAdd a line under Unreleased.\n"}
    assert commits(git_repo) == "0"


def test_a_long_refusal_is_trimmed_for_length_and_nothing_else(worker, shell, git_repo):
    """An unbounded refusal becomes an unbounded brief."""
    hook(git_repo, "#!/bin/sh\nprintf 'x%.0s' $(seq 5000)\nexit 1\n")
    (git_repo / "README.md").write_text("changed\n")
    got = actions.commit(worker, job_for(git_repo), {})
    assert got["refusal"] == "x" * 4000


def test_the_refusal_is_the_whole_next_brief(factory_main, settings_dir, git_repo):
    """A brief that wrapped the refusal in the spec would invite the turn to solve the wrong problem."""
    (settings_dir / "phases.yaml").write_text("phases:\n  run: {}\n")
    job = {**jobs.new("specs/document-the-ports.md", str(git_repo), "commit"),
           "path": str(git_repo), "turns": {"run": 1}}
    jobs.write(job)
    refusal = "CHANGELOG.md is unchanged.\nAdd a line under Unreleased.\n"
    factory_main.advance(job, {"ok": True, "refused": True, "refusal": refusal})
    moved = jobs.read(job["id"])
    assert (moved["stage"], moved["brief"], moved["last_refusal"]) == ("run", refusal, refusal)
    factory_main.step({"job_id": job["id"], "stage": "run", "epoch": moved["epoch"]})
    send = [p for fid, p in factory_main.worker.calls if fid == "harness::send"][-1]
    assert send["message"] == refusal
    assert send["session_id"] == send["idempotency_key"] == f"s_{job['id'][:8]}_run_2"
    assert send["options"]["metadata"]["fs_scope"]["root"] == str(git_repo)


def test_a_revision_ignores_the_completion_of_the_turn_before_it(factory_main, settings_dir,
                                                                git_repo):
    """A late run_1 completion would move a job whose run_2 is still working."""
    (settings_dir / "phases.yaml").write_text("phases:\n  run: {}\n")
    job = {**jobs.new("specs/document-the-ports.md", str(git_repo), "run"),
           "path": str(git_repo), "turns": {"run": 1}, "brief": "fix it"}
    jobs.write(job)
    factory_main.step({"job_id": job["id"], "stage": "run", "epoch": 0})
    stale = {"session_id": f"s_{job['id'][:8]}_run_1", "status": "completed", "terminal": True}
    assert "skipped" in factory_main.turn_completed(stale)
    assert jobs.read(job["id"])["session_id"] == f"s_{job['id'][:8]}_run_2"


# Bounded twice

def test_an_identical_refusal_stops_the_loop_before_the_limit():
    """An unconditional hook refuses every diff, so a second turn learns nothing and costs one."""
    job = {"stage": "commit", "revisions": 1, "last_refusal": "frozen until Monday\n"}
    to, why = next_stage(job, DEFAULT, {"refused": True, "refusal": "frozen until Monday\n"})
    assert to == "failed" and why.startswith("the refusal came back word for word")


def test_a_refusal_that_changed_goes_back_to_run():
    """A changed refusal is information: the agent moved something and the gate noticed."""
    job = {"stage": "commit", "revisions": 1, "last_refusal": "line 3"}
    assert next_stage(job, DEFAULT, {"refused": True, "refusal": "line 9"})[0] == "run"


def test_the_first_refusal_is_never_identical():
    """Comparing against nothing must not read as a repeat."""
    assert next_stage({"stage": "commit", "revisions": 0}, DEFAULT,
                      {"refused": True, "refusal": ""})[0] == "run"


def test_the_revision_loop_stops_at_two_and_names_the_stage_and_the_limit():
    """Two buys one fix and one correction of that fix; a third is a budget nobody set."""
    to, why = next_stage({"stage": "commit", "revisions": 2, "last_refusal": "a"}, DEFAULT,
                         {"refused": True, "refusal": "b"})
    assert to == "failed" and "commit" in why and "limit of 2" in why


def test_a_problem_fails_the_job_instead_of_revising():
    """No revision brings a worker back up, so a problem routed to run would pay for nothing."""
    to, why = next_stage({"stage": "commit", "revisions": 0}, DEFAULT,
                         {"ok": False, "error": "the ladder is down"})
    assert to == "failed" and "the ladder is down" in why


# Check before you act

def test_a_second_delivery_does_not_commit_twice(worker, shell, git_repo):
    """The queue can deliver commit twice, and a second git commit would fail or duplicate."""
    (git_repo / "README.md").write_text("changed\n")
    job = job_for(git_repo)
    assert actions.commit(worker, job, {})["committed"]
    shell.ran.clear()
    assert actions.commit(worker, job, {}) == {"ok": True, "committed": True}
    assert commits(git_repo) == "1"
    assert not any(args[:2] == ["git", "commit"] for args in shell.ran)


def test_a_branch_with_no_commits_is_a_problem(worker, shell, git_repo):
    """A pushed branch with no commits was the first rework run's whole output in ghola."""
    got = actions.commit(worker, job_for(git_repo), {})
    assert got["ok"] is False and "no commits" in got["error"]


def test_local_forge_never_pushes(worker, shell, git_repo):
    """A repository with no forge has no remote, and pushing to origin there fails every time."""
    (git_repo / "README.md").write_text("changed\n")
    actions.commit(worker, job_for(git_repo), {})
    assert not any(args[:2] in (["git", "push"], ["git", "ls-remote"]) for args in shell.ran)


def test_push_is_skipped_when_the_remote_already_has_head(worker, shell, git_repo):
    """A redelivered commit stage would push again for nothing, or fail on a protected branch."""
    (git_repo / "README.md").write_text("changed\n")
    git(git_repo, "commit", "-qam", "already")
    shell.remote_head = git(git_repo, "rev-parse", "HEAD").strip()
    got = actions.commit(worker, job_for(git_repo, forge="github"), {})
    assert got == {"ok": True, "committed": True, "pushed": False}
    assert not any(args[:2] == ["git", "push"] for args in shell.ran)


def test_push_runs_when_the_remote_differs(worker, shell, git_repo):
    """The no-op must key on the hash, or a stale remote branch would never update."""
    (git_repo / "README.md").write_text("changed\n")
    shell.remote_head = "0" * 40
    got = actions.commit(worker, job_for(git_repo, forge="github"), {})
    assert got == {"ok": True, "committed": True, "pushed": True}
    assert ["git", "push", "-u", "origin", BRANCH] in shell.ran


def test_a_failed_push_is_a_problem_never_a_refusal(worker, shell, git_repo):
    """A refusal sends the diff back for a paid turn, and no turn fixes a remote."""
    (git_repo / "README.md").write_text("changed\n")
    shell.push_fails = True
    got = actions.commit(worker, job_for(git_repo, forge="github"), {})
    assert got["ok"] is False and "push failed" in got["error"] and "refused" not in got


def test_the_github_gate_diffs_against_the_remote_base(worker, shell, git_repo):
    """A local base for a pushed repository reads a different change than the reviewer sees."""
    assert actions.base_ref(job_for(git_repo, forge="github")) == "origin/main"
    assert actions.base_ref(job_for(git_repo)) == "main"
