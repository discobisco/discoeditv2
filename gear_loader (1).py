# gear_loader.py
import os, json, re
from collections import OrderedDict

def _norm_key(s: str) -> str:
    s = re.sub(r"\s*/\s*", "/", s.strip())
    s = re.sub(r"\s{2,}", " ", s)
    return s.replace(" /", "/").replace("/ ", "/")

def _coerce_id(v):
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except Exception:
            return v
    if isinstance(v, str):
        vv = v.strip()
        if vv == "" or vv.lower() in {"nan", "none"}:
            return vv
        if vv.lower().startswith("0x"):
            try:
                return int(vv, 16)
            except Exception:
                return vv
        if re.fullmatch(r"-?\d+", vv):
            try:
                return int(vv)
            except Exception:
                return vv
    return v

def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_ce_style(path):
    data = _load_json(path)
    out = OrderedDict()
    for raw_k, items in data.items():
        k = _norm_key(raw_k)
        norm_items = []
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    idv = _coerce_id(it.get("id", it.get("value", "")))
                    label = it.get("label", it.get("name", it.get("text", str(idv))))
                    norm_items.append({"id": idv, "label": label})
        if norm_items:
            out[k] = norm_items
    return out

def _load_pair_list(path, key_name):
    data = _load_json(path)
    items_in = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                items_in = v
                break
    if not items_in and isinstance(data, list):
        items_in = data
    norm_items = []
    for it in items_in:
        if isinstance(it, dict):
            idv = _coerce_id(it.get("id", it.get("value", "")))
            label = it.get("label", it.get("name", it.get("text", str(idv))))
            norm_items.append({"id": idv, "label": label})
    return {_norm_key(key_name): norm_items} if norm_items else {}

def build_dropdown_registry(lookups_dir="lookups"):
    registry = OrderedDict()
    ce_candidates = ["gear_dropdowns.json", "lookups_from_ce_gear.json", "gear_lookups_index.json"]
    for fn in ce_candidates:
        p = os.path.join(lookups_dir, fn)
        if os.path.exists(p):
            dd = _load_ce_style(p)
            for k, v in dd.items():
                if k not in registry:
                    registry[k] = v
    overrides = [
        ("Edit Player/Shoes/Gear/Shoe Home", "shoes_home.json"),
        ("Edit Player/Shoes/Gear/Shoe Away", "shoes_away.json"),
        ("Edit Player/Shoes/Gear/Shoe Locked Vendor", "shoe_vendor_locked.json"),
        ("Edit Player/Shoes/Gear/Sock Length Home", "sock_length.json"),
        ("Edit Player/Shoes/Gear/Sock Length Away", "sock_length.json"),
    ]
    for key_name, fn in overrides:
        p = os.path.join(lookups_dir, fn)
        if os.path.exists(p):
            dd = _load_pair_list(p, key_name)
            registry.update(dd)
    pos_path = os.path.join(lookups_dir, "positions.json")
    if os.path.exists(pos_path):
        registry.update(_load_pair_list(pos_path, "Edit Player/Bio/Position"))
    return registry

def ensure_dropdowns_auto(lookups_dir="lookups", out_path="dropdowns_auto.json"):
    reg = build_dropdown_registry(lookups_dir)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    return out_path, len(reg)
