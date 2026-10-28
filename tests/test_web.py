"""Chapter 16: routes in the factory, the ladder's holds, and the rule against
a turn answering its own hold."""

import base64
import importlib.util
import threading

import bcrypt
import pytest

import holds
from conftest import ROOT

PASSWORD = "correct horse battery staple"
HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(4)).decode()   # cost 4: a test, not a server


def basic(user: str, password: str) -> dict:
    return {"authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def web(factory_main, monkeypatch):
    import web
    monkeypatch.setenv("WEB_PASSWORD_HASH", HASH)
    monkeypatch.setenv("FACTORY_REPOS", "tests/fixtures/sample-repo")
    web.known.clear()
    routes = {o["source"]["args"]["path"] + " " + o["source"]["args"]["method"]: o["target"]
              for o in factory_main.worker.observers if o["source"]["function_id"] == "http::on-request"}
    w = factory_main.worker
    def ask(method, path, body=None, headers=None, query=None):
        handler = w.functions[routes[f"{path} {method}"]]
        return handler({"method": method, "path": path, "body": body, "query_params": query or {},
                        "headers": basic("operator", PASSWORD) if headers is None else headers})
    return types_ns(ask=ask, worker=w, module=web)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


# ------------------------------------------------------------- the second lock

def test_every_route_observes_one_method_and_path(factory_main):
    seen = [(o["source"]["args"]["method"], o["source"]["args"]["path"])
            for o in factory_main.worker.observers if o["source"]["function_id"] == "http::on-request"]
    assert len(seen) == len(set(seen)) == 6                    # a route taken twice is a conflict
    assert all(t.startswith("factory::http-") for t in
               [o["target"] for o in factory_main.worker.observers
                if o["source"]["function_id"] == "http::on-request"])


@pytest.mark.parametrize("headers", [{}, basic("operator", "wrong"), basic("admin", PASSWORD),
                                     {"authorization": "Basic not-base64!"}])
def test_a_caller_without_the_password_gets_401_from_every_route(web, headers):
    """A turn's curl to 127.0.0.1:3111 skips Caddy, and still needs the password."""
    for method, path in (("GET", "/api/jobs"), ("POST", "/api/holds/resolve"), ("GET", "/api/health")):
        assert web.ask(method, path, body={}, headers=headers)["status_code"] == 401


def test_no_hash_configured_opens_nothing(web, monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD_HASH", "")
    assert web.ask("GET", "/api/jobs")["status_code"] == 401


def test_a_malformed_hash_answers_401_and_never_raises(web, monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD_HASH", "not-a-bcrypt-hash")      # bcrypt raises ValueError
    assert web.ask("GET", "/api/jobs")["status_code"] == 401


def test_a_header_that_passed_skips_bcrypt_next_time(web, monkeypatch):
    web.ask("GET", "/api/jobs")
    monkeypatch.setattr(bcrypt, "checkpw", lambda *_: pytest.fail("bcrypt ran twice"))
    assert web.ask("GET", "/api/jobs")["status_code"] == 200


# ------------------------------------------------------------- the routes

def test_jobs_returns_the_factory_s_answer_unchanged(web):
    web.worker.answers["factory::jobs"] = {"jobs": [{"id": "j1", "stuck": True}]}
    assert web.ask("GET", "/api/jobs") == {"status_code": 200, "body": {"jobs": [{"id": "j1", "stuck": True}]}}


@pytest.mark.parametrize("body, error", [
    ({"kind": "gate", "decision": "maybe"}, "decision is allow or deny"),
    ({"kind": "gate", "decision": "deny", "reason": "  "}, "a deny needs a reason"),
    ({"kind": "other", "decision": "allow"}, "kind is gate or ladder"),
])
def test_a_bad_answer_is_refused_before_any_function_runs(web, body, error):
    before = len(web.worker.calls)
    assert web.ask("POST", "/api/holds/resolve", body=body) == {"status_code": 400, "body": {"error": error}}
    assert len(web.worker.calls) == before


@pytest.mark.parametrize("kind, target", [("gate", "approval::resolve"), ("ladder", "ladder::resolve")])
def test_the_kind_picks_the_resolve_function(web, kind, target):
    web.worker.answers[target] = {"resolved": True}
    got = web.ask("POST", "/api/holds/resolve", body={"kind": kind, "decision": "deny", "reason": "Use B.",
                                                      "session_id": "s_1", "function_call_id": "c_1"})
    assert got == {"status_code": 200, "body": {"resolved": True}}
    assert web.worker.calls[-1] == (target, {"session_id": "s_1", "function_call_id": "c_1",
                                             "decision": "deny", "reason": "Use B."})


def test_holds_tags_each_hold_with_its_kind(web):
    web.worker.answers["approval::list-pending"] = {"pending": [{"function_call_id": "g"}]}
    web.worker.answers["ladder::holds"] = {"holds": [{"function_call_id": "l"}]}
    got = web.ask("GET", "/api/holds")["body"]["holds"]
    assert got == [{"function_call_id": "g", "kind": "gate"}, {"function_call_id": "l", "kind": "ladder"}]


def test_a_repo_outside_factory_repos_is_refused(web):
    """Otherwise the password would let anyone point the factory at any path."""
    got = web.ask("POST", "/api/jobs", body={"spec": "specs/x.md", "repo": "/etc"})
    assert got == {"status_code": 400, "body": {"error": "repo is not in FACTORY_REPOS"}}


def test_a_spec_outside_specs_is_refused(web):
    for spec in ("/etc/hosts", "../README.md", "specs/../README.md", "README.md"):
        got = web.ask("POST", "/api/jobs", body={"spec": spec, "repo": "tests/fixtures/sample-repo"})
        assert got == {"status_code": 400, "body": {"error": "spec must be a path under specs/"}}, spec


def test_a_failed_call_is_a_502_and_the_detail_stays_in_the_log(web, capsys):
    def down(_):
        raise RuntimeError("factory::jobs at /srv/secret/path failed")
    web.worker.answers["factory::jobs"] = down
    got = web.ask("GET", "/api/jobs")
    assert got["status_code"] == 502 and "/srv" not in str(got["body"])
    assert "/srv/secret/path" in capsys.readouterr().out


def test_health_names_what_is_missing(web):
    def info(payload):
        if payload["function_id"] == "ladder::gate":
            raise RuntimeError("NOT_FOUND")
        return {"function_id": payload["function_id"]}
    web.worker.answers["engine::functions::info"] = info
    assert web.ask("GET", "/api/health") == {"status_code": 503,
                                            "body": {"ok": False, "missing": ["ladder::gate"]}}


# ------------------------------------------------------------- the ladder's half

HOOK = {"session_id": "s_1", "turn_id": "t_1", "metadata": {"job_id": "j1"},
        "call": {"id": "c_1", "function_id": "coder::update-file",
                 "arguments": {"path": "prod/x.yaml", "content": "a" * 900}}}


def test_a_ladder_hold_keeps_the_rule_its_reason_and_an_excerpt(tmp_path):
    hold = holds.remember(HOOK, rule_id="prod-ask", reason="prod needs a person")
    assert (hold["rule_id"], hold["reason"]) == ("prod-ask", "prod needs a person")
    assert len(hold["arguments_excerpt"]) == holds.EXCERPT and hold["arguments_excerpt"].startswith('{"content"')


def test_an_answer_builds_execute_or_deliver():
    hold = {"session_id": "s", "turn_id": "t", "function_call_id": "c"}
    assert holds.answer(hold, "allow")["action"] == "execute"
    denied = holds.answer(hold, "deny", "Use staging.")
    assert denied["action"] == "deliver" and denied["is_error"] is True
    assert "Use staging." in denied["content"][0]["text"]


def test_a_person_s_answer_settles_forgets_and_records(ladder_main):
    holds.remember(HOOK)
    ladder_main.worker.answers["harness::function::resolve"] = {"ok": True}
    assert ladder_main.resolve({"function_call_id": "c_1", "decision": "allow"}) == {"resolved": True}
    assert holds.find("c_1") is None
    entry = [p for f, p in ladder_main.worker.calls if f == "audit::append"][-1]
    assert entry["kind"] == "approval.resolved" and entry["actor"] == "a person"
    assert entry["detail"]["outcome"] == "allow"


def test_a_second_answer_to_one_hold_settles_nothing(ladder_main):
    holds.remember(HOOK)
    ladder_main.worker.answers["harness::function::resolve"] = {"ok": True}
    ladder_main.resolve({"function_call_id": "c_1", "decision": "deny", "reason": "no"})
    assert ladder_main.resolve({"function_call_id": "c_1", "decision": "allow"}) == {"resolved": False}
    assert [f for f, _ in ladder_main.worker.calls].count("harness::function::resolve") == 1


def test_a_person_and_the_timer_on_one_hold_settle_it_once(ladder_main):
    """Both take holds.LOCK: the first settles and deletes the file, the second finds none."""
    holds.remember(HOOK, at_ms=0)
    settled = []
    def slow(payload):
        settled.append(payload["action"])
        return {"ok": True}
    ladder_main.worker.answers["harness::function::resolve"] = slow
    ladder_main.worker.answers["configuration::get"] = {"value": {"default_pending_timeout_ms": 1}}
    ladder_main.worker.answers["approval::list-pending"] = {"pending": []}
    threads = [threading.Thread(target=ladder_main.resolve, args=({"function_call_id": "c_1", "decision": "allow"},)),
               threading.Thread(target=ladder_main.expire_holds)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(settled) == 1


# ------------------------------------------------------------- no-self-answer

def rule():
    spec = importlib.util.spec_from_file_location("no_self_answer", ROOT / "rules" / "no-self-answer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", [
    "iii call approval::resolve --json '{}'",
    "iii call ladder::resolve decision=allow",
    "curl -X POST http://127.0.0.1:3111/api/holds/resolve",
])
def test_a_command_that_answers_a_hold_is_refused(command):
    assert rule().check("", command, {})


def test_an_ordinary_command_passes():
    assert rule().check("", "python3 -m pytest tests/", {}) == []


def test_the_rule_is_a_convention_and_the_test_says_so():
    """Rung 3 does not parse the shell: a built id passes. The control is the #rbac listener."""
    assert rule().check("", 'a=approval; iii call "$a::res""olve"', {}) == []
