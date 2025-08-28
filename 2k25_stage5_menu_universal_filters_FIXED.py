
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor — Stage 5.5 (Resilient Menu + Filters + Lookups)
- Does not auto-exit on missing symbols. Synthesizes shims.
- Prioritizes Offsets.json and enforces Body→Weight as float @ 0xF8.
- Loads gear lookups from provided JSONs for drop-downs.
"""
import os, sys, json, csv, re, importlib.util, traceback, tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Base editor discovery and import
# ---------------------------------------------------------------------------
CANDIDATES = [
    "2k25_roster_editor_stage4_players_gear_hotzones.py",
    "2k25_roster_editor_stage3_players_badges_grouped.py",
    "2k25_roster_editor_stage3_players_dropdowns.py",
    "2k25_roster_editor_stage3_players.py",
    "2k25_roster_editor_stage2.py",
    "2k25_player_patcher_gui.py",
    "2k25_player_patcher_gui_no_comments.py",
    "2k25_player_patcher_gui_patched_homepage.py",
]

def _select_base_dialog():
    root = tk.Tk(); root.withdraw()
    try:
        path = filedialog.askopenfilename(
            title="Select base editor .py",
            filetypes=[("Python","*.py"),("All files","*.*")])
    finally:
        root.destroy()
    return path

def _load_base():
    # Prefer local candidates. Fall back to file picker.
    for name in CANDIDATES:
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            sel = p; break
    else:
        sel = _select_base_dialog()
    if not sel:
        raise RuntimeError("No base editor selected or found beside launcher.")
    spec = importlib.util.spec_from_file_location("base_editor", sel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod, sel

try:
    base, BASE_PATH = _load_base()
except Exception as e:
    # Show error but keep window open for visibility.
    print("Failed to load base:", e, file=sys.stderr)
    traceback.print_exc()
    messagebox.showerror("Stage 5 launcher", f"Failed to load base editor:\n{e}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Shim missing API expected by Stage‑5/6
# ---------------------------------------------------------------------------

def _read_bits_shim(gm, addr, byte_off, bit_off, bit_len):
    """
    Generic little‑endian bit reader using gm.read_bytes().
    gm must expose read_bytes(addr, n)->bytes.
    """
    start = addr + int(byte_off)
    nbits = int(bit_off) + int(bit_len)
    nbytes = (nbits + 7) // 8
    raw = gm.read_bytes(start, max(nbytes, 1))
    val = int.from_bytes(raw, 'little', signed=False)
    mask = ((1 << bit_len) - 1) & ((1 << 64) - 1) if bit_len < 64 else (1 << bit_len) - 1
    return (val >> bit_off) & mask

def _write_bits_shim(gm, addr, byte_off, bit_off, bit_len, value):
    """
    Read‑modify‑write of a bitfield using gm.read_bytes/write_bytes.
    """
    start = addr + int(byte_off)
    nbits = int(bit_off) + int(bit_len)
    nbytes = (nbits + 7) // 8
    buf = bytearray(gm.read_bytes(start, max(nbytes, 1)))
    cur = int.from_bytes(buf, 'little', signed=False)
    mask = ((1 << bit_len) - 1) << bit_off
    newv = (cur & ~mask) | ((int(value) << bit_off) & mask)
    gm.write_bytes(start, int(newv).to_bytes(len(buf), 'little'))
    return True

def _resolve_table_base_shim(gm):
    """
    Best‑effort pointer resolution: if base editor exposes resolve_player_base(),
    use it. Else if it defines PLAYER_PTR_CHAINS and PLAYER_TABLE_RVA/PLAYER_STRIDE,
    compute base = gm.base_addr + rva and return.
    """
    # Try common names first
    if hasattr(base, "resolve_player_base"):
        return base.resolve_player_base(gm)  # type: ignore
    if hasattr(base, "PLAYER_TABLE_RVA") and getattr(gm, "base_addr", None):
        return gm.base_addr + int(getattr(base, "PLAYER_TABLE_RVA"))
    # As a last resort return 0 to avoid crashing callers.
    return 0

def _raw_to_attr_passthru(v, bitlen): return int(v)
def _attr_to_raw_passthru(v, bitlen): return int(v)
def _raw_to_tendency_passthru(v, bitlen): return int(v)
def _tendency_to_raw_passthru(v, bitlen): return int(v)

# Attach shims only if missing
if not hasattr(base, "read_bits"):
    base.read_bits = _read_bits_shim     # type: ignore[attr-defined]
if not hasattr(base, "write_bits"):
    base.write_bits = _write_bits_shim   # type: ignore[attr-defined]
if not hasattr(base, "resolve_table_base"):
    base.resolve_table_base = _resolve_table_base_shim  # type: ignore[attr-defined]
if not hasattr(base, "raw_to_attr"):
    base.raw_to_attr = _raw_to_attr_passthru  # type: ignore[attr-defined]
if not hasattr(base, "attr_to_raw"):
    base.attr_to_raw = _attr_to_raw_passthru  # type: ignore[attr-defined]
if not hasattr(base, "raw_to_tendency"):
    base.raw_to_tendency = _raw_to_tendency_passthru  # type: ignore[attr-defined]
if not hasattr(base, "tendency_to_raw"):
    base.tendency_to_raw = _tendency_to_raw_passthru  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Offsets loader override
# ---------------------------------------------------------------------------
def _load_offsets_override():
    """
    Returns (base_info, categories) to mimic prior Stage‑3/4 API.
    - base_info: dict with stride and pointer info if present.
    - categories: dict[str, list[dict]] parsed from Offsets.json.
    Enforces Body→Weight as float at 0xF8.
    """
    path = os.path.join(HERE, "Offsets.json")
    if not os.path.isfile(path):
        raise FileNotFoundError("Offsets.json not found beside launcher.")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    base_info = data.get("Base", {})
    cats = {k: v for k, v in data.items() if k != "Base"}
    # Normalize offsets to ints, add defaults
    for cat, arr in list(cats.items()):
        if not isinstance(arr, list):
            continue
        for fld in arr:
            if "offset" in fld and isinstance(fld["offset"], str):
                s = fld["offset"].strip()
                fld["offset"] = int(s, 16) if s.lower().startswith("0x") else int(s or "0")
            fld["startBit"] = int(fld.get("startBit", 0) or 0)
            fld["length"]   = int(fld.get("length", 0) or 0)
    # Enforce Weight
    body = cats.get("Body", [])
    for fld in body:
        if str(fld.get("name","")).strip().lower() == "weight":
            fld["offset"] = 0xF8
            fld["length"] = 32
            fld["varType"] = "Float"
            fld["startBit"] = 0
            break
    return base_info, cats

base.load_offsets = _load_offsets_override  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Lookups loader (gear, socks, vendors, etc.)
# ---------------------------------------------------------------------------
LOOKUP_FILES = [
    "gear_dropdowns.json",
    "shoes_gear.json",
    "gear_lookups_index.json",
    "shoes_home.json",
    "shoe_vendor_locked.json",
    "sock_length.json",
    "positions.json",
    "lookups_from_ce_gear.json",
    "Team_Data__Use_this_address_for_team_addresses_.json",
]

def _load_lookups():
    lookups = {}
    for name in LOOKUP_FILES:
        p = os.path.join(HERE, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                lookups[name] = json.load(f)
        except Exception:
            # Keep going; bad/missing file should not kill UI
            traceback.print_exc()
    # Offer flattened helper namespaces as well
    # e.g. lookups['shoes_home'] from gear_dropdowns.json if present
    gd = lookups.get("gear_dropdowns.json", {})
    if isinstance(gd, dict):
        for k, v in gd.items():
            lookups.setdefault(k, v)
    # shoes_gear.json uses "Edit Player/..." keys; keep as-is
    base.LOOKUPS = lookups  # type: ignore[attr-defined]
    return lookups

LOOKUPS = _load_lookups()

# ---------------------------------------------------------------------------
# Tools menu (Filters + Sort + Batch stubs)
# ---------------------------------------------------------------------------

class ToolsMenu:
    def __init__(self, app, base_mod):
        self.app = app
        self.base = base_mod
        self.gm = getattr(app, "gm", None) or getattr(app, "mem", None)  # tolerate naming
        # Attach to menubar
        menubar = getattr(app, "menubar", None)
        if menubar is None:
            menubar = tk.Menu(app)
            app.config(menu=menubar)
        tools = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Tools", menu=tools)
        tools.add_command(label="Filter Builder…", command=self.open_filters)
        tools.add_command(label="Sort Players…", command=self.open_sort)
        tools.add_separator()
        tools.add_command(label="Reload lookups", command=_load_lookups)
        tools.add_command(label="Reload Offsets.json", command=lambda: base.load_offsets())
        self._filter_win = None
        self._sort_win = None

    # ---- Filter window ----
    def open_filters(self):
        if self._filter_win and self._filter_win.winfo_exists():
            self._filter_win.lift(); return
        self._filter_win = FilterBuilder(self.app, self.base)
    # ---- Sort window ----
    def open_sort(self):
        if self._sort_win and self._sort_win.winfo_exists():
            self._sort_win.lift(); return
        self._sort_win = SortWindow(self.app, self.base)

def _iter_players(app):
    # Try to extract player list from common base App implementations
    if hasattr(app, "players"):
        return list(getattr(app, "players"))
    if hasattr(app, "model") and hasattr(app.model, "players"):
        return list(app.model.players)
    # Fallback: empty
    return []

def _player_label(p):
    # tolerate various player object shapes
    idx = getattr(p, "index", getattr(p, "idx", getattr(p, "i", 0)))
    first = getattr(p, "first", getattr(p, "first_name", ""))
    last = getattr(p, "last", getattr(p, "last_name", ""))
    team = getattr(p, "team_name", getattr(p, "team", ""))
    return f"{idx:04d} — {first} {last} ({team})"

class FilterBuilder(tk.Toplevel):
    def __init__(self, app, base_mod):
        super().__init__(app)
        self.title("Filter Builder")
        self.geometry("880x560")
        self.app = app
        self.base = base_mod
        self.gm = getattr(app, "gm", None) or getattr(app, "mem", None)
        self.base_info, self.categories = self.base.load_offsets()
        # Top controls
        top = ttk.Frame(self); top.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(top, text="Combine:").pack(side=tk.LEFT)
        self.var_mode = tk.StringVar(value="AND")
        ttk.Combobox(top, values=["AND","OR"], textvariable=self.var_mode, state="readonly", width=6).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Apply", command=self.apply).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Export CSV", command=self.export_csv).pack(side=tk.LEFT, padx=6)
        # Body
        body = ttk.Frame(self); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        # Rule list
        left = ttk.Frame(body); left.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(left, text="Rules").pack(anchor="w")
        self.lst = tk.Listbox(left, height=20, exportselection=False)
        self.lst.pack(fill=tk.Y, expand=False)
        btns = ttk.Frame(left); btns.pack(fill=tk.X, pady=(6,0))
        ttk.Button(btns, text="Add…", command=self.add_rule).pack(side=tk.LEFT)
        ttk.Button(btns, text="Edit…", command=self.edit_rule).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Delete", command=self.delete_rule).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Clear", command=self.clear_rules).pack(side=tk.LEFT, padx=4)
        # Players list
        center = ttk.Frame(body); center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0))
        ttk.Label(center, text="Players").pack(anchor="w")
        self.tree = ttk.Treeview(center, columns=("idx","name","team","face"), show="headings")
        for col, w in (("idx",60),("name",220),("team",200),("face",80)):
            self.tree.heading(col, text=col.upper()); self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)
        # Populate initially
        self.rules = []
        self.refresh_players()

    def refresh_players(self, result=None):
        self.tree.delete(*self.tree.get_children())
        players = result or _iter_players(self.app)
        for p in players:
            idx = getattr(p, "index", getattr(p, "idx", 0))
            name = f"{getattr(p,'first','')} {getattr(p,'last','')}"
            team = getattr(p,'team_name', '')
            face = getattr(p,'face_id', getattr(p,'face', ''))
            self.tree.insert("", "end", values=(idx, name, team, face))

    def add_rule(self):
        RuleEditor(self, self.base, self.categories, on_done=self._add_rule_done)

    def edit_rule(self):
        sel = self._sel_index()
        if sel is None: return
        RuleEditor(self, self.base, self.categories, existing=self.rules[sel], on_done=lambda r: self._edit_rule_done(sel, r))

    def delete_rule(self):
        sel = self._sel_index()
        if sel is None: return
        self.rules.pop(sel); self._refresh_rules_list()

    def clear_rules(self):
        self.rules.clear(); self._refresh_rules_list()

    def _sel_index(self):
        try:
            s = self.lst.curselection()
            return s[0] if s else None
        except Exception:
            return None

    def _refresh_rules_list(self):
        self.lst.delete(0, tk.END)
        for r in self.rules:
            self.lst.insert(tk.END, f"{r['cat']} / {r['field']} {r['op']} {r.get('val')} {r.get('val2','')}")

    def _add_rule_done(self, rule):
        if rule: self.rules.append(rule); self._refresh_rules_list()

    def _edit_rule_done(self, idx, rule):
        if rule: self.rules[idx] = rule; self._refresh_rules_list()

    def _field_spec(self, cat, field):
        for f in self.categories.get(cat, []):
            if str(f.get("name","")).strip().lower() == field.strip().lower():
                off = int(f.get("offset",0)); sb = int(f.get("startBit",0)); ln = int(f.get("length",8))
                return off, sb, ln
        return None

    def _read_value(self, p, cat, field):
        spec = self._field_spec(cat, field)
        if not spec: return None
        off, sb, ln = spec
        addr = getattr(p, "addr", 0) + off
        # Special case: weight float
        if cat == "Body" and field.lower() == "weight":
            try:
                b = self.gm.read_bytes(addr, 4)
                import struct
                return struct.unpack('<f', b)[0]
            except Exception:
                return None
        try:
            return base.read_bits(self.gm, addr, 0, sb, ln)
        except Exception:
            return None

    def _eval_rule(self, p, r):
        v = self._read_value(p, r['cat'], r['field'])
        if v is None: return False
        op = r['op']
        if op == "between":
            a = float(r.get('val',0)); b = float(r.get('val2',0))
            return a <= float(v) <= b
        # numeric compare
        try: tgt = float(r.get('val',0))
        except Exception: return False
        if op == "==": return float(v) == tgt
        if op == "!=": return float(v) != tgt
        if op == "<":  return float(v) <  tgt
        if op == "<=": return float(v) <= tgt
        if op == ">":  return float(v) >  tgt
        if op == ">=": return float(v) >= tgt
        return False

    def apply(self):
        players = _iter_players(self.app)
        if not self.rules:
            self.refresh_players(players); return
        res = []
        mode = self.var_mode.get()
        for p in players:
            vals = [self._eval_rule(p, r) for r in self.rules]
            ok = all(vals) if mode == "AND" else any(vals)
            if ok: res.append(p)
        self.refresh_players(res)

    def export_csv(self):
        players = [self.tree.item(i)['values'] for i in self.tree.get_children("")]
        if not players:
            messagebox.showinfo("Export CSV", "No rows to export."); return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")], initialfile="filtered_players.csv")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["Index","Name","Team","FaceID"]); w.writerows(players)
        messagebox.showinfo("Export CSV", f"Wrote {path}")

class RuleEditor(tk.Toplevel):
    def __init__(self, master, base_mod, categories, existing=None, on_done=None):
        super().__init__(master)
        self.title("Rule")
        self.geometry("520x260")
        self.base = base_mod
        self.categories = categories
        self.on_done = on_done
        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Label(frm, text="Category").grid(row=0, column=0, sticky="w")
        self.var_cat = tk.StringVar(value=list(categories.keys())[0])
        ttk.Combobox(frm, values=list(categories.keys()), textvariable=self.var_cat, state="readonly", width=28).grid(row=0, column=1, sticky="w")
        ttk.Label(frm, text="Field").grid(row=1, column=0, sticky="w")
        self.var_field = tk.StringVar()
        self.cmb_field = ttk.Combobox(frm, values=[], textvariable=self.var_field, state="readonly", width=28)
        self.cmb_field.grid(row=1, column=1, sticky="w")
        self.var_cat.trace_add("write", lambda *_: self._reload_fields())
        ttk.Label(frm, text="Operator").grid(row=2, column=0, sticky="w")
        self.var_op = tk.StringVar(value="==")
        ttk.Combobox(frm, values=["==","!=","<","<=",">",">=","between"], textvariable=self.var_op, state="readonly", width=10).grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="Value").grid(row=3, column=0, sticky="w")
        self.var_val = tk.StringVar(); ttk.Entry(frm, textvariable=self.var_val, width=12).grid(row=3, column=1, sticky="w")
        ttk.Label(frm, text="Value 2 (between)").grid(row=4, column=0, sticky="w")
        self.var_val2 = tk.StringVar(); ttk.Entry(frm, textvariable=self.var_val2, width=12).grid(row=4, column=1, sticky="w")
        btns = ttk.Frame(frm); btns.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10,0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side=tk.LEFT)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=6)
        self._reload_fields()
        if existing:
            self.var_cat.set(existing.get("cat", self.var_cat.get()))
            self.var_field.set(existing.get("field", ""))
            self.var_op.set(existing.get("op", "=="))
            self.var_val.set(str(existing.get("val","")))
            self.var_val2.set(str(existing.get("val2","")))

    def _reload_fields(self):
        cat = self.var_cat.get()
        fields = [str(f.get("name","")) for f in self.categories.get(cat, [])]
        self.cmb_field['values'] = fields
        if fields and not self.var_field.get():
            self.var_field.set(fields[0])

    def _ok(self):
        cat, field, op = self.var_cat.get(), self.var_field.get(), self.var_op.get()
        if not field:
            self.destroy(); return
        out = {"cat":cat, "field":field, "op":op, "val":self.var_val.get(), "val2":self.var_val2.get()}
        if self.on_done: self.on_done(out)
        self.destroy()

class SortWindow(tk.Toplevel):
    def __init__(self, app, base_mod):
        super().__init__(app)
        self.title("Sort Players")
        self.geometry("600x340")
        self.app = app
        self.base = base_mod
        self.gm = getattr(app, "gm", None) or getattr(app, "mem", None)
        _, self.categories = self.base.load_offsets()
        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        ttk.Label(frm, text="Field:").grid(row=0, column=0, sticky="w")
        self.var_field = tk.StringVar()
        # Provide simple fields list: Index, First, Last, Team plus any Body/Attributes fields
        fields = ["Index","First","Last","Team","Face ID"]
        for cat in ("Body","Attributes","Tendencies","Durability"):
            for f in self.categories.get(cat, []):
                fields.append(f"{cat}:{f.get('name')}")
        self.cmb_field = ttk.Combobox(frm, values=fields, textvariable=self.var_field, state="readonly", width=36)
        self.cmb_field.grid(row=0, column=1, sticky="w")
        self.var_desc = tk.IntVar(value=0)
        ttk.Checkbutton(frm, text="Descending", variable=self.var_desc).grid(row=1, column=1, sticky="w")
        ttk.Button(frm, text="Apply", command=self.apply).grid(row=2, column=1, sticky="w", pady=8)
        self.tree = ttk.Treeview(self, columns=("idx","name","team","face","val"), show="headings")
        for col, w in (("idx",60),("name",220),("team",200),("face",80),("val",100)):
            self.tree.heading(col, text=col.upper()); self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

    def _key(self, p, field):
        if field == "Index": return getattr(p,"index",0)
        if field == "First": return str(getattr(p,"first","")).lower()
        if field == "Last":  return str(getattr(p,"last","")).lower()
        if field == "Team":  return str(getattr(p,"team_name","")).lower()
        if field == "Face ID": return int(getattr(p,"face_id",0))
        # Composite cat:name
        if ":" in field:
            cat, name = field.split(":",1)
            # weight special case
            if cat == "Body" and name.strip().lower() == "weight":
                addr = getattr(p,"addr",0) + 0xF8
                try:
                    b = self.gm.read_bytes(addr, 4)
                    import struct; return struct.unpack('<f', b)[0]
                except Exception: return 0.0
            # general bitfield
            for f in self.categories.get(cat, []):
                if str(f.get("name","")).strip().lower() == name.strip().lower():
                    off = int(f.get("offset",0)); sb = int(f.get("startBit",0)); ln = int(f.get("length",8))
                    try:
                        return int(base.read_bits(self.gm, getattr(p,"addr",0)+off, 0, sb, ln))
                    except Exception:
                        return 0
        return 0

    def apply(self):
        players = _iter_players(self.app)
        field = self.var_field.get() or "Index"
        desc = bool(self.var_desc.get())
        rows = []
        for p in players:
            val = self._key(p, field)
            rows.append((getattr(p,"index",0), f"{getattr(p,'first','')} {getattr(p,'last','')}", getattr(p,'team_name',''), getattr(p,'face_id',0), val, p))
        rows.sort(key=lambda r: r[4], reverse=desc)
        self.tree.delete(*self.tree.get_children(""))
        for r in rows:
            self.tree.insert("", "end", values=r[:-1])

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if sys.platform != "win32":
        messagebox.showerror("Platform", "Windows only."); return
    # Expect base.App or base.PlayerEditorApp
    AppClass = getattr(base, "App", None) or getattr(base, "PlayerEditorApp", None)
    if AppClass is None:
        messagebox.showerror("Base editor", "Base editor does not expose App class."); return
    app = AppClass()
    ToolsMenu(app, base)
    try:
        app.mainloop()
    except Exception as e:
        traceback.print_exc()
        messagebox.showerror("Stage 5", f"Unhandled error:\n{e}")

if __name__ == "__main__":
    main()
