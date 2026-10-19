from __future__ import annotations

import json
from pathlib import Path


class PhaseState:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def is_done(self, name: str) -> bool:
        return name in self.load().get("completed", [])

    def mark_done(self, name: str) -> None:
        data = self.load()
        data.setdefault("completed", [])
        if name not in data["completed"]:
            data["completed"].append(name)
            self.save(data)

    def completed(self) -> list:
        return self.load().get("completed", [])