"""The record's head in every request body the factory opens.

A script that rebuilds every hash after an edit passes verify_audit.py. The
forge's copy of the head is what that rebuilt chain contradicts.
"""

import actions
import repos

HEAD = "c0ffee" + "0" * 58
JOB = {"id": "0123abcd" * 4, "repo": "/code/scratch", "branch": "factory/document-the-ports-0123abcd",
       "title": "# Document the ports", "spec": "specs/document-the-ports.md",
       "settings": {**repos.DEFAULTS, "forge": "local"}}


def test_the_anchor_line_names_the_head():
    assert actions.anchor(HEAD) == f"record head: {HEAD}"


def test_an_unanswered_head_says_so_rather_than_writing_nothing():
    """A missing line would read as a factory from before chapter 11."""
    assert actions.anchor("") == "record head: unknown (the audit worker did not answer)"


def test_the_request_body_ends_with_the_anchor_line():
    body = actions.request_body(JOB, HEAD)
    assert body.endswith(f"\n\nrecord head: {HEAD}\n")
    assert "Document the ports" in body


def test_an_empty_record_anchors_at_genesis(worker):
    worker.answers["audit::summary"] = {"entries": 0, "head": None}
    assert actions.record_head(worker) == "0" * 64


def test_publish_writes_the_head_into_the_request_and_records_the_publish(worker):
    """The head is read before the request opens, so the forge holds it."""
    disk = {}

    def create(payload):
        disk.update({f["path"]: f["content"] for f in payload["files"]})
        return {"results": [{"path": f["path"], "success": True} for f in payload["files"]]}

    worker.answers |= {
        "audit::summary": {"entries": 3, "head": HEAD},
        "coder::read-file": lambda p: (_ for _ in ()).throw(RuntimeError("no such file")),
        "coder::create-file": create,
        "audit::append": {},
    }
    got = actions.publish(worker, JOB, {})
    assert got["opened"] is True
    text, = disk.values()
    assert f"record head: {HEAD}" in text
    entry, = [p for fid, p in worker.calls if fid == "audit::append"]
    assert entry["kind"] == "published" and entry["detail"]["record_head"] == HEAD
    assert entry["subject"] == JOB["id"]
