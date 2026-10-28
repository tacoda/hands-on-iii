"""Chapter 15: the same compose file on a server, behind one door."""

import configparser
import re

import yaml

from conftest import ROOT

COMPOSE = yaml.safe_load((ROOT / "worker-compose.yaml").read_text())


def unit() -> configparser.ConfigParser:
    parsed = configparser.ConfigParser(strict=False, interpolation=None)
    parsed.optionxform = str
    parsed.read_string((ROOT / "deploy" / "hands-on-iii.service").read_text())
    return parsed


def test_each_key_goes_only_to_the_worker_that_reads_it():
    """One .env gave llm-router the forge token and the github worker three model keys."""
    containers = COMPOSE["containers"]
    assert containers["llm-router"]["env_file"] == ["./.env.models"]
    assert containers["github"]["env_file"] == ["./.env.forge"]
    assert containers["planner"]["env_file"] == ["./.env.planner"]
    holders = {k for k, c in containers.items() if c.get("env_file")}
    assert holders == {"llm-router", "github", "planner"}


def test_the_templates_hold_names_and_no_values():
    for name, keys in ((".env.models.example", {"ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"}),
                       (".env.forge.example", {"GH_TOKEN"}),
                       (".env.planner.example", {"ANTHROPIC_API_KEY"})):
        lines = [l for l in (ROOT / name).read_text().splitlines() if l and not l.startswith("#")]
        assert {l.split("=")[0] for l in lines} == keys
        assert all(l.endswith("=") for l in lines)


def test_git_ignores_every_real_env_file():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert {".env", ".env.models", ".env.forge", ".env.planner"} <= set(ignored)


def test_the_worker_manager_listens_on_loopback_only():
    """The engine's port for workers: anything that reaches it can register a function."""
    manager = COMPOSE["engine"]["workers"]["iii-worker-manager"]
    assert manager["host"] == "127.0.0.1" and manager["port"] == 49134


def test_the_unit_runs_compose_in_the_foreground_and_restarts_only_a_crash():
    """Compose in the background makes systemd mark the service done, and nothing restarts the engine."""
    service = unit()["Service"]
    assert service["Type"] == "simple"
    assert service["ExecStart"] == "/usr/local/bin/iii compose --up"
    assert "&" not in service["ExecStart"]
    assert service["Restart"] == "on-failure"
    assert service["User"] == "hands-on-iii"
    # configparser keeps only the last Environment line, so read them all from the file.
    text = (ROOT / "deploy" / "hands-on-iii.service").read_text()
    environment = [line.split("=", 1)[1] for line in text.splitlines() if line.startswith("Environment=")]
    # compose resolves ${PWD}, and systemd sets none.
    assert "PWD=/srv/hands-on-iii/app" in environment
    # The harness's pre_run runs python3, which needs PyYAML from the venv.
    assert any(e.startswith("PATH=/srv/hands-on-iii/app/.venv/bin:") for e in environment)
    writable = service["ReadWritePaths"].split()
    assert "/srv/hands-on-iii/app/state" in writable
    # worktree 0.4.3 keeps its worktrees under the project.
    assert "/srv/hands-on-iii/app/worktrees" in writable
    # The app tree stays read-only, so a prove turn can't rewrite rules/ or scripts/.
    assert not {"/srv/hands-on-iii", "/srv/hands-on-iii/app"} & set(writable)


def test_the_unit_bounds_a_crash_loop():
    assert unit()["Unit"]["StartLimitBurst"] == "5"
    assert unit()["Unit"]["StartLimitIntervalSec"] == "300"


def test_the_door_answers_ping_and_nothing_else():
    """Nothing behind Caddy reaches iii yet: the console stays private on 3113."""
    caddy = (ROOT / "deploy" / "Caddyfile").read_text()
    assert 'respond "ok" 200' in caddy and "respond 404" in caddy
    assert not re.search(r"reverse_proxy|3113|49134", caddy)


def test_the_backup_doc_keeps_its_archive_private():
    doc = (ROOT / "docs" / "backup.md").read_text()
    assert "umask 077" in doc and "/etc/hands-on-iii" in doc and "date +%Y%m%dT%H%M%S" in doc
