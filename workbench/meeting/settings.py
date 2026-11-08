"""Isolated experiment settings. Secrets stay outside the source tree."""
import json,os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def settings():
    config=json.loads(Path(os.environ.get("MEETING_CONFIG",str(CONFIG_PATH))).read_text(encoding="utf-8"))
    if os.environ.get("TMCRA_MEMORY_BYPASS")=="1":
        config.update(memory_recall_enabled=False,tmcra_write=False,learner_memory_scopes=[])
    return config

