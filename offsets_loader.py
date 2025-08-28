
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

def _read_json(p: Path) -> Dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def _ensure_body_weight(struct: Dict[str, Any]) -> None:
    # Ensure Body block exists with Weight float at 0xF8. Do not overwrite if present.
    body = struct.setdefault("Body", [])
    names = {x.get("name") for x in body if isinstance(x, dict)}
    if "Weight" not in names:
        body.append({"name": "Weight", "offset": "0xF8", "varType": "Float"})
    # Normalize Height/Wingspan to keep consistent shape if provided by user
    # No hard assumption about their offsets here.

def load_offsets(path: Optional[str | Path] = None) -> Dict[str, Any]:
    # Priority: Offsets.json beside this file, then CWD/Offsets.json
    candidates = []
    here = Path(__file__).resolve().parent
    candidates.append(here / "Offsets.json")
    candidates.append(Path.cwd() / "Offsets.json")
    if path:
        candidates.insert(0, Path(path))

    data: Dict[str, Any] | None = None
    for p in candidates:
        if p.exists():
            try:
                data = _read_json(p)
                break
            except Exception:
                continue
    if data is None:
        # empty baseline
        data = {"bases": [], "fields": {}, "bitfields": {}}

    _ensure_body_weight(data)
    return data
