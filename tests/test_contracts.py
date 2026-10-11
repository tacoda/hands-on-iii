"""Chapter 10's contracts, the verdict guard, and the INTERRUPT hatch. All pure."""

import pytest

import contracts
from contracts import interrupt, read, text_of, verdict_guard

PROOF = "PROVEN: yes\n\n- [x] README.md gains one section\n      $ grep -n Ports README.md\n      12:## Ports\n"


# The proven contract

def test_a_yes_with_a_command_under_it_is_a_yes():
    """A contract that downgraded real evidence would teach everyone to ignore it."""
    assert read(PROOF, "proven") == {"value": "yes", "claimed": "yes", "why": ""}


def test_yes_without_a_command_is_unproven():
    """Delete the `needs` entry under `proven` and a claim with nothing under it reads as proof."""
    got = read("PROVEN: yes\n\nEverything works. I checked it all.\n", "proven")
    assert got["value"] == "unproven" and got["claimed"] == "yes"
    assert "no command" in got["why"]


@pytest.mark.parametrize("value", ["no", "partial"])
def test_no_and_partial_need_no_command(value):
    """Only a yes is a claim of success, so only a yes must show its evidence."""
    assert read(f"PROVEN: {value}\n\nThe email criterion is unmet.\n", "proven")["value"] == value


def test_a_command_mentioned_in_prose_is_not_a_command_line():
    """The evidence is a line that starts with `$`, not a sentence that names one."""
    assert read("PROVEN: yes\nI ran $ make test and it passed.\n", "proven")["value"] == "unproven"


@pytest.mark.parametrize("text", ["", "Everything works.", "PROVEN: definitely\n$ ls\n",
                                  "PROVEN:\n$ ls\n", "PROVEN: **yes**\n$ ls\n"])
def test_an_unparseable_proof_is_unproven_never_a_pass(text):
    """A prompt edit that changed the output format would otherwise read as approval for weeks."""
    got = read(text, "proven")
    assert got["value"] == "unproven"
    assert got["why"] == "no readable answer, and that is never a pass"


def test_the_marker_must_start_a_line():
    """A turn that mentions PROVEN: mid-sentence has not declared one."""
    assert read("I will answer PROVEN: yes below.\n$ ls\n", "proven")["value"] == "unproven"


def test_a_downgrade_keeps_what_the_turn_claimed():
    """Without `claimed`, a reader sees what the answer became and never what was said."""
    assert read("PROVEN: maybe\n", "proven")["claimed"] == "maybe"
    assert read("VERDICT: concerns\nerror handling is weak\n", "verdict")["claimed"] == "concerns"


def test_the_marker_is_read_case_insensitively_in_the_value():
    """`PROVEN: YES` from a shouting model is still a yes, with evidence."""
    assert read("PROVEN: YES\n$ ls\n", "proven")["value"] == "yes"


# The verdict contract

def test_a_pass_needs_no_place():
    """A clean review has nothing to point at."""
    assert read("VERDICT: pass\n", "verdict")["value"] == "pass"


@pytest.mark.parametrize("value", ["concerns", "blocker"])
def test_an_objection_with_a_path_and_line_stands(value):
    """A review that names a place can be acted on."""
    text = f"VERDICT: {value}\n\n- README.md:14 the port is wrong.\n"
    assert read(text, "verdict")["value"] == value


@pytest.mark.parametrize("value", ["concerns", "blocker"])
def test_an_objection_that_names_no_place_is_unreadable(value):
    """A few concerns about error handling, with no place to look, is a mood."""
    got = read(f"VERDICT: {value}\n\nA few concerns about error handling.\n", "verdict")
    assert got["value"] == "unreadable" and got["claimed"] == value


@pytest.mark.parametrize("text", ["", "Looks fine to me.", "VERDICT: lgtm\n"])
def test_an_unparseable_verdict_is_unreadable(text):
    """No marker, or a value off the list, must never read as a pass."""
    assert read(text, "verdict")["value"] == "unreadable"


def test_place_is_the_first_path_and_line():
    """The blocked job's reason names one place, the first, so a person knows where to start."""
    text = "VERDICT: blocker\n\n- src/app.py:9 drops the error.\n- README.md:1 wrong.\n"
    assert contracts.place(text) == "src/app.py:9"
    assert contracts.place("no place here") == ""


# text_of

def test_text_of_joins_the_text_blocks_of_the_final_message():
    """Reading a `text` key on the input found nothing, and the guard returned quietly."""
    data = {"generated": {"message": {"content": [
        {"type": "text", "text": "VERDICT: pass"}, {"type": "text", "text": "fine"}]}}}
    assert text_of(data) == "VERDICT: pass\nfine"


def test_text_of_skips_thinking_blocks():
    """A model reasoning about which verdict to give has not given one."""
    data = {"generated": {"message": {"content": [
        {"type": "thinking", "thinking": "VERDICT: blocker? README.md:1"},
        {"type": "text", "text": "VERDICT: pass"}]}}}
    assert text_of(data) == "VERDICT: pass"


def test_text_of_returns_a_plain_string_unchanged():
    """A turn result on the text contract is the final message's text, a string."""
    assert text_of("PROVEN: no") == "PROVEN: no"
    assert text_of({"generated": {"message": {"content": "VERDICT: pass"}}}) == "VERDICT: pass"
    assert text_of(None) == "" and text_of({}) == ""


# The guard

def hook(phase, text):
    return {"point": "post_generate", "session_id": "s_0123abcd_review_1",
            "metadata": {"phase": phase, "job_id": "0123abcd"},
            "generated": {"message": {"content": [{"type": "text", "text": text}]}}}


def test_the_guard_annotates_a_downgraded_proof():
    """In ghola a real prove turn said yes with no command; the guard recorded what it became."""
    got = verdict_guard(hook("prove", "PROVEN: yes\nAll good.\n"))
    assert got == {"decision": "continue",
                   "annotations": {"factory.proven": "unproven", "factory.claimed": "yes"}}


def test_the_guard_annotates_a_review_by_its_contract():
    """BY_PHASE maps review to verdict; a guard that graded it as a proof would read nothing."""
    got = verdict_guard(hook("review", "VERDICT: blocker\n- README.md:1 wrong\n"))
    assert got["annotations"] == {"factory.verdict": "blocker", "factory.claimed": "blocker"}


@pytest.mark.parametrize("data", [
    hook("plan", "PROVEN: yes"),          # a phase with no contract
    hook("run", "VERDICT: pass"),
    hook("prove", ""),                    # nothing to grade
    hook("prove", "INTERRUPT: which port?"),
    {"generated": {"message": {"content": "VERDICT: pass"}}},  # no metadata at all
])
def test_the_guard_only_ever_continues_and_annotates_only_a_graded_check(data):
    """post-generate observes; a guard that refused would turn a weak review into a failed job."""
    assert verdict_guard(data) == {"decision": "continue"}


def test_the_guard_and_the_stage_call_one_parser():
    """Two parsers would one day disagree about a turn's answer."""
    text = "VERDICT: concerns\nThe naming is off.\n"
    assert verdict_guard(hook("review", text))["annotations"]["factory.verdict"] == \
        read(text, "verdict")["value"]


# The INTERRUPT hatch

def test_an_opening_interrupt_line_is_a_question():
    """The one way out the run prompt gives a turn that is stuck."""
    assert interrupt("INTERRUPT: there is no mail credential. Drop the email?") == \
        "there is no mail credential. Drop the email?"
    assert interrupt("\n  INTERRUPT: which port?\n") == "which port?"


@pytest.mark.parametrize("text", [
    "Done. No INTERRUPT: needed.",
    "Added the section.\nINTERRUPT: should I also do the email?",
    "interrupt: lower case is not the marker",
    "",
])
def test_interrupt_counts_only_as_the_opening_line(text):
    """A summary that says "no INTERRUPT: needed" must not stop a job."""
    assert interrupt(text) == ""
