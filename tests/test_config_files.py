from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_every_config_file_carries_the_four_keys_the_engine_reads():
    """The configuration worker skips a file that lacks id, name, description, or value,
    and the owning worker then writes its own defaults over it. A gate config with no
    name ran the gate in manual mode with its stock rules, and nothing said so."""
    for path in sorted(CONFIG.glob("*.yaml")):
        entry = yaml.safe_load(path.read_text())
        missing = sorted({"id", "name", "description", "value"} - set(entry))
        assert not missing, f"{path.name} lacks {missing}"
        assert entry["id"] == path.stem, f"{path.name} registers as {entry['id']}"
