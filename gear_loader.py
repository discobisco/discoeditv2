
# gear_loader.py
# Robust loader for 2K25 gear/lookups JSONs.
# - Supports three JSON shapes:
#   1) Canonical flat files, e.g. shoes_home.json with [{"value": <int>, "label": <str>}, ...]
#   2) Aggregated canonical keys, e.g. gear_dropdowns.json with {"shoes_home": [...], ...}
#   3) CE-style namespaced paths, e.g. shoes_gear.json or lookups_from_ce_gear.json with
#      {"Edit Player/Shoes/Gear/Shoe Home": [{"id": <int>, "label"/"name": <str>}, ...], ...}
#
# Priority of sources (highest to lowest):
#   - Files listed in gear_lookups_index.json (explicit user intent per key)
#   - gear_dropdowns.json (canonical aggregate)
#   - shoes_gear.json (CE-style path aggregate)
#   - lookups_from_ce_gear.json (CE-style, alt schema with "name")
#
# Output API:
#   registry = GearLookups(root_dir).load()
#   registry.get("shoes_home") -> list[{"id": int, "label": str}]
#   registry.available() -> sorted list of keys
#   registry.resolve_label("shoes_home", 0) -> "2K Generic"
#   registry.resolve_id("shoes_home", "2K Generic") -> 0
#
# Keys we normalize and support:
#   shoes_home, shoes_away, shoe_vendor_locked,
#   sock_length_home, sock_length_away,
#   undershirt, undershirt_home_color, undershirt_away_color,
#   shorts, shorts_preferred_type, shorts_length, shorts_home_color, shorts_away_color,
#   positions
#
from __future__ import annotations
import json, os, re
from typing import Dict, List, Tuple, Iterable

_CANONICAL_KEYS = {
    "Edit Player/Shoes/Gear/Shoe Home": "shoes_home",
    "Edit Player/Shoes/Gear/Shoe Away": "shoes_away",
    "Edit Player/Shoes/Gear/Shoe Locked Vendor": "shoe_vendor_locked",
    "Edit Player/Shoes/Gear/Sock Length Home": "sock_length_home",
    "Edit Player/Shoes/Gear/Sock Length Away": "sock_length_away",
    "Edit Player/Shoes/Gear/Undershirt": "undershirt",
    "Edit Player/Shoes/Gear/Undershirt Home Color": "undershirt_home_color",
    "Edit Player/Shoes/Gear/Undershirt Away Color": "undershirt_away_color",
    "Edit Player/Shoes/Gear/Shorts": "shorts",
    "Edit Player/Shoes/Gear/Shorts Preferred Type": "shorts_preferred_type",
    "Edit Player/Shoes/Gear/Shorts Length": "shorts_length",
    "Edit Player/Shoes/Gear/Shorts Home Color": "shorts_home_color",
    "Edit Player/Shoes/Gear/Shorts Away Color": "shorts_away_color",
    # Extras that sometimes appear elsewhere
    "Edit Player/Vitals/Position": "positions",
}

# Build a normalized mapping where we strip extra spaces around '/'
def _normalize_path_key(s: str) -> str:
    parts = [p.strip() for p in s.split('/')]
    return '/'.join(parts)

_CANONICAL_KEYS_NORM = {_normalize_path_key(k): v for k, v in _CANONICAL_KEYS.items()}

def _to_items(seq):
    """Normalize a list of dicts with 'id' or 'value', and 'label' or 'name'."""
    out = []
    for obj in seq:
        if not isinstance(obj, dict):
            # allow raw number or string
            item_id = obj
            item_label = str(obj)
        else:
            item_id = obj.get('id', obj.get('value'))
            item_label = obj.get('label', obj.get('name', str(item_id)))
        # best-effort int cast where possible
        try:
            if isinstance(item_id, str) and item_id.isdigit():
                item_id = int(item_id)
        except Exception:
            pass
        out.append({'id': item_id, 'label': str(item_label)})
    return out

class GearLookups:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.lookups_dir = os.path.join(root_dir, "lookups")
        self._data: Dict[str, List[dict]] = {}

    # ---- public API ----
    def load(self):
        # Highest priority: explicit index -> individual files
        self._load_from_index()

        # Next: aggregated canonical file
        self._load_if_present("gear_dropdowns.json", mode="canonical")

        # Next: CE-style long-path aggregate (id+label)
        self._load_if_present("shoes_gear.json", mode="ce_paths_label")

        # Next: CE-style long-path aggregate (id+name)
        self._load_if_present("lookups_from_ce_gear.json", mode="ce_paths_name")

        # Optional standalone helpers
        self._load_if_present("positions.json", mode="flat_list", key="positions")
        return self

    def get(self, key: str) -> List[dict]:
        return self._data.get(key, [])

    def available(self):
        return sorted(self._data.keys())

    def resolve_label(self, key: str, item_id) -> str | None:
        for row in self._data.get(key, []):
            if row.get('id') == item_id:
                return row.get('label')
        return None

    def resolve_id(self, key: str, label: str) -> int | None:
        label = label.strip().lower()
        for row in self._data.get(key, []):
            if row.get('label','').strip().lower() == label:
                return row.get('id')
        return None

    # ---- loaders ----
    def _load_from_index(self):
        idx_path = os.path.join(self.lookups_dir, "gear_lookups_index.json")
        if not os.path.isfile(idx_path):
            return
        with open(idx_path, "r", encoding="utf-8") as fh:
            idx = json.load(fh)
        # idx maps canonical key -> filename
        for key, fname in idx.items():
            p = os.path.join(self.lookups_dir, fname)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._data.setdefault(key, _to_items(data))
                elif isinstance(data, dict) and key in data:
                    self._data.setdefault(key, _to_items(data[key]))
            except Exception:
                # keep going on any corrupt file
                continue

    def _load_if_present(self, fname: str, mode: str, key: str|None=None):
        p = os.path.join(self.lookups_dir, fname)
        # also allow files in root for quick tests
        if not os.path.isfile(p):
            p = os.path.join(self.root_dir, fname)
            if not os.path.isfile(p):
                return
        with open(p, "r", encoding="utf-8") as fh:
            payload = json.load(fh)

        if mode == "canonical":  # dict of canonical_key -> list[{value,label}/...]
            if not isinstance(payload, dict):
                return
            for k, seq in payload.items():
                if isinstance(seq, list):
                    self._data.setdefault(k, _to_items(seq))

        elif mode == "flat_list" and key:  # list-only
            if isinstance(payload, list):
                self._data.setdefault(key, _to_items(payload))

        elif mode in ("ce_paths_label", "ce_paths_name"):
            if not isinstance(payload, dict):
                return
            # Normalize path keys then map to canonical
            for raw_k, seq in payload.items():
                norm = _normalize_path_key(raw_k)
                canonical = _CANONICAL_KEYS_NORM.get(norm)
                if not canonical or not isinstance(seq, list):
                    continue
                # Normalize items; schema differs only by 'label' vs 'name' key
                rows = _to_items(seq)
                self._data.setdefault(canonical, rows)
        else:
            return
