"""Load the ladder's rules. Each rule is two files with one name: `x.md` holds
the words in its frontmatter, and an optional `x.py` beside it holds the
predicate, found by filename.

Two layers, read from disk on every call: `team` from this repo's `rules/`,
which travels to every target, and `project` from the target repository's
`.agents/rules/`. `parse` takes text and returns a Rule, so tests need no disk.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[3]
TEAM = ROOT / "rules"
PROJECT = Path(".agents") / "rules"
POLICIES = ("refuse", "ask", "warn")

# A rung is carried only when something runs it. Chapter 9's delivery gate
# adds rung 4 here, and only then may a rule claim it.
CARRIED = {0: "prose in the charter", 3: "ladder::gate at harness::hook::pre-trigger"}
ELSEWHERE = {1: "rung 1 is the grant in settings/phases.yaml, not a rule file",
             2: "rung 2 is the target repository's .agents/settings.json, not a rule file",
             4: "nothing carries rung 4 until chapter 9's delivery gate",
             5: "rung 5 is CI, outside this machine"}
FRONT = re.compile(r"\A---\n(.*?)\n---[ \t]*\n?(.*)\Z", re.S)


@dataclass
class Rule:
    id: str
    layer: str
    description: str = ""
    why: str = ""
    policy: str = "refuse"
    escape: str = ""
    paths: tuple[str, ...] = ()
    claimed: tuple[int, ...] = ()
    rungs: tuple[int, ...] = ()   # the claimed rungs the loader accepted
    script: str = ""              # absolute path of the predicate, or ""
    refused: list[str] = field(default_factory=list)
    source: str = ""


def parse(text: str, name: str, layer: str, script: str = "", source: str = "") -> Rule:
    rule = Rule(id=name, layer=layer, script=script, source=source)
    match = FRONT.match(text)
    try:
        head = yaml.safe_load(match.group(1)) if match else None
    except yaml.YAMLError as error:
        rule.refused.append(f"{name}: the frontmatter is not YAML ({error.problem}), "
                            "so no rung carries it")
        return rule
    if not isinstance(head, dict):
        rule.refused.append(f"{name}: no frontmatter between --- lines, so no rung carries it")
        return rule

    rule.id = str(head.get("id") or name)
    rule.layer = str(head.get("layer") or layer)
    rule.description = str(head.get("description") or "").strip()
    rule.why = str(head.get("why") or "").strip()
    rule.policy = str(head.get("policy") or "refuse").strip().lower()
    rule.escape = str(head.get("escape") or "").strip()
    paths = head.get("paths") or []
    rule.paths = tuple(str(p) for p in ([paths] if isinstance(paths, str) else paths))

    given = head.get("rung", [0])
    given = given if isinstance(given, list) else [given]
    claimed = []
    for value in given:
        if isinstance(value, bool) or not isinstance(value, int):
            rule.refused.append(f"{rule.id}: rung {value!r} is not a number from 0 to 5")
        elif value not in claimed:
            claimed.append(value)
    rule.claimed = tuple(sorted(claimed))

    if rule.policy not in POLICIES:
        rule.refused.append(f"{rule.id}: policy {rule.policy!r} is not refuse, ask, or warn, "
                            "so no rung carries it")
        return rule

    stem = Path(source).stem or name
    accepted = []
    for rung in rule.claimed:
        if rung not in CARRIED:
            why = ELSEWHERE.get(rung, f"rung {rung} does not exist")
            rule.refused.append(f"{rule.id}: rung {rung} refused: {why}")
        elif rung >= 2 and not script:
            rule.refused.append(f"{rule.id}: rung {rung} refused: no {stem}.py sits beside "
                                f"{stem}.md, so this rule is prose only")
        elif rule.policy == "ask" and rung not in (0, 3):
            rule.refused.append(f"{rule.id}: rung {rung} refused: policy ask needs a person, "
                                "and only rung 3 can park a call for one")
        else:
            accepted.append(rung)
    rule.rungs = tuple(accepted)
    return rule


def from_dir(directory: Path, layer: str) -> list[Rule]:
    if not directory.is_dir():
        return []
    found = []
    for md in sorted(directory.glob("*.md")):
        predicate = md.with_suffix(".py")
        try:
            text = md.read_text()
        except (OSError, UnicodeDecodeError) as error:
            # One bad file never stops the others from loading.
            found.append(Rule(id=md.stem, layer=layer, source=str(md),
                              refused=[f"{md.stem}: unreadable ({error}), so no rung carries it"]))
            continue
        found.append(parse(text, md.stem, layer,
                           str(predicate) if predicate.is_file() else "", str(md)))
    return found


def load(repo: str = "", team: Path = TEAM) -> list[Rule]:
    project = from_dir(Path(repo) / PROJECT, "project") if repo else []
    return project + from_dir(Path(team), "team")
