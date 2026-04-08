from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class JsonStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._lock = Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.data_dir / name

    def read_json(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.exists():
            self.write_json(name, default)
            return default

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.write_json(name, default)
            return default

    def write_json(self, name: str, payload: Any) -> None:
        path = self._path(name)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        with self._lock:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
