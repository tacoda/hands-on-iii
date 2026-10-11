"""scripts/verify_audit.py: the record read from disk, with no engine.

Entries are built here the way chain.ts builds them, so a test needs no Node.
"""

import hashlib
import json

import verify_audit

GENESIS = "0" * 64


def chain(n: int) -> list[dict]:
    entries, prev = [], GENESIS
    for seq in range(n):
        body = {"v": 1, "seq": seq, "prev": prev, "kind": "stage.entered", "at": 1000 + seq,
                "actor": "factory", "subject": "job", "detail": {"epoch": seq}}
        entries.append(sealed(body))
        prev = entries[-1]["hash"]
    return entries


def sealed(body: dict) -> dict:
    body = {k: v for k, v in body.items() if k != "hash"}
    return {**body, "hash": hashlib.sha256(verify_audit.canonical(body).encode()).hexdigest()}


def lines(entries: list[dict]) -> list[str]:
    return [verify_audit.canonical(e) for e in entries]


def test_an_intact_record_prints_its_count_intact_and_the_last_entry():
    """The Run it reads these three facts off one line."""
    code, said = verify_audit.report(lines(chain(4)))
    assert code == 0
    assert said == "4 entries, intact, verified through entry 3."


def test_an_empty_record_is_intact():
    """A fresh checkout has no state/audit/ yet, and that is not a break."""
    assert verify_audit.report([]) == (0, "0 entries, intact, verified through entry -1.")


def test_one_changed_character_is_named_by_entry_number():
    """A boolean answer would hide where trust ends."""
    entries = chain(5)
    entries[2]["actor"] = "factorx"
    code, said = verify_audit.report(lines(entries))
    assert code == 1
    assert "entry 2 does not match its hash, so it was edited" in said
    assert "Verified through entry 1" in said


def test_rehashing_the_edit_moves_the_break_one_entry_later():
    """The Run it's second break: the next prev no longer matches."""
    entries = chain(5)
    entries[2] = sealed({**entries[2], "actor": "factorx"})
    result = verify_audit.verify(lines(entries))
    assert result == {"ok": False, "through": 2,
                      "problem": "entry 3 does not follow the entry before it"}


def test_verify_reports_the_first_break_not_a_later_one():
    entries = chain(6)
    entries[1]["actor"] = "x"
    entries[4]["actor"] = "y"
    assert verify_audit.verify(lines(entries))["problem"].startswith("entry 1 ")


def test_a_deleted_entry_is_caught_even_when_the_next_prev_is_fixed():
    """With prev outside the hash, this edit would pass."""
    entries = chain(4)
    del entries[1]
    entries[1] = {**entries[1], "seq": 1, "prev": entries[0]["hash"]}
    assert verify_audit.verify(lines(entries))["problem"] == \
        "entry 1 does not match its hash, so it was edited"


def test_a_torn_last_line_fails_and_names_it():
    """A crash mid-write; chaining past it would hide whatever it held."""
    code, said = verify_audit.report(lines(chain(3)) + ['{"v":1,"seq":3,"pr'])
    assert code == 1
    assert "entry 3 cannot be read" in said


def test_an_anchor_in_the_chain_passes():
    entries = chain(4)
    code, said = verify_audit.report(lines(entries), anchor=entries[2]["hash"])
    assert code == 0 and "contains the anchor" in said


def test_a_rebuilt_chain_fails_the_anchor_the_forge_holds():
    """Rewriting every later hash passes plain verify. The anchor is what it contradicts."""
    entries = chain(4)
    anchor = entries[3]["hash"]
    rebuilt, prev = [], GENESIS
    for e in entries:
        body = {**e, "prev": prev, "actor": "factorx" if e["seq"] == 1 else e["actor"]}
        rebuilt.append(sealed(body))
        prev = rebuilt[-1]["hash"]
    assert verify_audit.report(lines(rebuilt))[0] == 0
    code, said = verify_audit.report(lines(rebuilt), anchor=anchor)
    assert code == 1
    assert "does not contain the anchor" in said


def test_an_anchor_past_the_break_does_not_count():
    """Trust ends at the break, so a hash after it vouches for nothing."""
    entries = chain(4)
    entries[1]["actor"] = "x"
    assert verify_audit.anchored(lines(entries), 0, entries[3]["hash"]) is False


def test_main_reads_the_folder_and_exits_non_zero_on_a_break(tmp_path, capsys):
    """It works as a CI step only if the exit code carries the answer."""
    entries = chain(3)
    entries[1]["kind"] = "stage.enterex"
    (tmp_path / "audit.jsonl").write_text("\n".join(lines(entries)) + "\n")
    assert verify_audit.main(["--dir", str(tmp_path)]) == 1
    assert "entry 1" in capsys.readouterr().out
    (tmp_path / "audit.jsonl").write_text("\n".join(lines(chain(3))) + "\n")
    assert verify_audit.main(["--dir", str(tmp_path)]) == 0


def test_compose_starts_audit_before_both_of_its_writers():
    """Without start_after, the first refusal after --up can arrive before anything listens."""
    import yaml
    compose = yaml.safe_load((verify_audit.ROOT / "worker-compose.yaml").read_text())["containers"]
    audit = compose["audit"]
    assert audit["worker"] == "path://./workers/audit"
    assert audit["environment"]["AUDIT_DIR"] == "${PWD}/state/audit"
    for writer in ("ladder", "factory"):
        assert "audit" in compose[writer]["start_after"], f"{writer} can start before audit"


def test_the_audit_worker_registers_five_functions_and_binds_post_trigger_with_hyphens():
    """A misspelt hook type binds nothing, and the record silently misses every call."""
    source = (verify_audit.ROOT / "workers" / "audit" / "src" / "index.ts").read_text()
    for fid in ("audit::append", "audit::read", "audit::verify", "audit::summary",
                "audit::record_call"):
        assert f'registerFunction("{fid}"' in source
    for kind in ("harness::hook::post-trigger", "approval::pending-created",
                 "approval::pending-resolved"):
        assert f'type: "{kind}", function_id: "audit::record_call"' in source
