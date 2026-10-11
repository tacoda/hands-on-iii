"""Chapter 10's job document: one markdown file per job, a section per phase."""

import document
import jobs

SPEC = "# Document the ports\n\n## What\n\nAdd a section.\n\n## Acceptance criteria\n\n- one section\n"


def test_a_document_starts_as_a_copy_of_the_spec():
    """A document that referenced specs/ would change when someone edited the spec."""
    text = document.start(SPEC)
    assert document.sections(text) == {"spec": SPEC.strip()}


def test_a_section_does_not_swallow_the_next_heading():
    """Slicing to the next marker leaves every section ending with the next one's title."""
    text = document.put(document.put(document.start(SPEC), "plan", "Edit README.md."),
                        "work", "Added it.")
    got = document.sections(text)
    assert got["plan"] == "Edit README.md."
    assert "What was built" not in got["plan"] and "The plan" not in got["spec"]
    assert got["work"] == "Added it."


def test_the_specs_own_headings_stay_inside_the_spec():
    """Sections split on markers, so a `## What` in the spec invents no section."""
    text = document.put(document.start(SPEC), "plan", "p")
    assert "## Acceptance criteria" in document.sections(text)["spec"]
    assert set(document.sections(text)) == {"spec", "plan"}


def test_a_section_body_may_carry_its_own_headings():
    """A plan with `## Steps` in it is still one plan."""
    text = document.put(document.put(document.start(SPEC), "plan", "## Steps\n\n1. one"),
                        "work", "w")
    assert document.sections(text)["plan"] == "## Steps\n\n1. one"


def test_a_phase_run_again_replaces_its_section_in_place():
    """Two "What was built" sections leave a reviewer guessing which is current."""
    text = document.put(document.start(SPEC), "work", "first try")
    text = document.put(text, "proof", "PROVEN: no")
    text = document.put(text, "work", "second try")
    assert text.count("<!-- factory:work -->") == 1 and text.count("## What was built") == 1
    assert document.sections(text)["work"] == "second try"
    assert list(document.sections(text)) == ["spec", "work", "proof"]
    assert document.sections(text)["proof"] == "PROVEN: no"


def test_a_retitled_heading_keeps_its_section():
    """The marker names the section, so a phase can rename its heading."""
    text = document.put(document.start(SPEC), "plan", "p").replace("## The plan", "## How")
    assert document.sections(text)["plan"] == "p"
    replaced = document.put(text, "plan", "q")
    assert "## How" not in replaced and document.sections(replaced)["plan"] == "q"


def test_an_empty_section_is_missing():
    """A turn that ends with its section empty has not produced a section."""
    text = document.put(document.start(SPEC), "work", "   ")
    assert document.missing(text, ["spec", "work", "proof"]) == ["work", "proof"]


def test_load_creates_the_document_from_the_spec_once(tmp_path, monkeypatch):
    """The document is state/documents/<id>.md, beside the job records, never the spec itself."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path / "jobs"))
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC)
    job = jobs.new(str(spec), str(tmp_path), "plan")
    text = document.load(job)
    assert document.path(job) == tmp_path / "documents" / f"{job['id']}.md"
    document.save(job, document.put(text, "plan", "p"))
    spec.write_text("changed\n")
    assert document.sections(document.load(job))["spec"] == SPEC.strip()
