
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor — Stage 5.4 (Menu Tools + Universal Filter Builder)
Windows-only. Requires an existing editor script (Stage 3.x or Stage 4) and offsets.json.

Adds:
- Top menubar "Tools": Undo/Redo, Batch Ops…, Sort Players…, Filter Builder…, Draft Class ▶ (quick actions)
- Universal Filter Builder: build rule sets over ANY category/field from offsets.json, plus built-ins.
  * Operators: ==, !=, <, <=, >, >=, between (numeric); is/is not (boolean); contains/not contains/equals/starts/ends/regex (strings)
  * Combine: AND / OR
  * Display mapping toggle for Attributes (25–99) and Tendencies (0–100)
  * Presets: Save / Load JSON
  * Apply to Players tab, Export CSV

Draft Class submenu remains for quick default filters.
"""

import os, sys, csv, json, re, importlib.util, unicodedata, tkinter as tk
from tkinter import ttk, filedialog, messagebox

# -------------------------
# Locate and load base editor
# -------------------------

CANDIDATES = [
    "2k25_roster_editor_stage4_players_gear_hotzones_scrolled.py",
    "2k25_roster_editor_stage3_players_badges_grouped.py",
    "2k25_roster_editor_stage3_players_dropdowns.py",
    "2k25_roster_editor_stage3_players.py",
    "2k25_roster_editor_stage2.py",
    "2k25_player_patcher_gui.py",
    "2k25_player_patcher_gui_no_comments.py",
    "2k25_player_patcher_gui_patched_homepage.py",
]

def _find_base_script():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in CANDIDATES:
        p = os.path.join(here, name)
        if os.path.isfile(p):
            return p
    try:
        root = tk.Tk(); root.withdraw()
        p = filedialog.askopenfilename(title="Select base editor .py", filetypes=[("Python","*.py"),("All","*.*")])
        root.destroy()
        return p if p else None
    except Exception:
        return None

BASE_PATH = _find_base_script()
if not BASE_PATH:
    print("Base editor script not found beside launcher. Place this file next to your editor and run again.")
    sys.exit(1)

spec = importlib.util.spec_from_file_location("base_editor", BASE_PATH)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)  # type: ignore

REQUIRES = ("GameMemory","read_bits","write_bits","resolve_table_base","PLAYER_STRIDE","PLAYER_PTR_CHAINS","load_offsets")
for sym in REQUIRES:
    if not hasattr(base, sym):
        print(f"Missing symbol in base editor: {sym}")
        sys.exit(1)

# Optional maps from base (if present)
POSITION_MAP = getattr(base, "POSITION_MAP", {0:"PG",1:"SG",2:"SF",3:"PF",4:"C"})
PLAY_TYPES   = getattr(base, "PLAY_TYPES", {0:"None",1:"Isolation",2:"Isolation Point",3:"Isolation Wing",4:"P&R Ball Handler",5:"P&R Point",6:"P&R Wing",7:"P&R Roll Man",8:"Post Up Low",9:"Post Up High",10:"Guard Post Up",11:"Cutter",12:"Handoff Receiver",13:"Handoff Passer",14:"Mid Range",15:"3 PT"})

# -------------------------
# Undo/Redo patch
# -------------------------

class UndoRedo:
    def __init__(self, base_mod):
        self.base = base_mod
        self._orig_write_bits = base_mod.write_bits
        self.undo_stack = []  # (gm, addr, byte_off, bit_off, bit_len, old_val, new_val)
        self.redo_stack = []
        self.enabled = True

    def wrapper_write_bits(self, gm, addr, byte_off, bit_off, bit_len, new_val):
        if not self.enabled:
            return self._orig_write_bits(gm, addr, byte_off, bit_off, bit_len, new_val)
        try:
            old_val = self.base.read_bits(gm, addr, byte_off, bit_off, bit_len)
        except Exception:
            old_val = None
        ok = self._orig_write_bits(gm, addr, byte_off, bit_off, bit_len, new_val)
        if ok and old_val is not None and old_val != new_val:
            self.undo_stack.append((gm, addr, byte_off, bit_off, bit_len, old_val, new_val))
            self.redo_stack.clear()
        return ok

    def undo(self):
        if not self.undo_stack:
            return False
        gm, addr, byte_off, bit_off, bit_len, old_val, new_val = self.undo_stack.pop()
        self.enabled = False
        try:
            ok = self._orig_write_bits(gm, addr, byte_off, bit_off, bit_len, old_val)
        finally:
            self.enabled = True
        if ok:
            self.redo_stack.append((gm, addr, byte_off, bit_off, bit_len, old_val, new_val))
        return ok

    def redo(self):
        if not self.redo_stack:
            return False
        gm, addr, byte_off, bit_off, bit_len, old_val, new_val = self.redo_stack.pop()
        self.enabled = False
        try:
            ok = self._orig_write_bits(gm, addr, byte_off, bit_off, bit_len, new_val)
        finally:
            self.enabled = True
        if ok:
            self.undo_stack.append((gm, addr, byte_off, bit_off, bit_len, old_val, new_val))
        return ok

undo_manager = UndoRedo(base)
base.write_bits = undo_manager.wrapper_write_bits  # patch

# -------------------------

# Name normalization for strict first+last matching only
def _norm_name(s: str) -> str:
    s = s or ""
    # ASCII fold and collapse whitespace
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = " ".join(s.strip().lower().split())
    # drop common suffix tokens
    toks = [t for t in s.replace(",", "").split() if t not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(toks)


# Shared helpers
# -------------------------

def rebuild_player_tree(app):
    """Rebuilds the Players tab tree from app.players, preserving selection if possible."""
    sel_idx = None
    try:
        sel = app.tree.selection()
        if sel:
            vals = app.tree.item(sel[0], "values")
            sel_idx = int(vals[0])
    except Exception:
        pass
    try:
        app.tree.delete(*app.tree.get_children())
        for p in app.players:
            app.tree.insert("", tk.END, values=(p.index, p.first, p.last, p.face_id, p.team_name))
        if sel_idx is not None:
            for iid in app.tree.get_children():
                vals = app.tree.item(iid, "values")
                if int(vals[0]) == sel_idx:
                    app.tree.selection_set(iid)
                    app.tree.see(iid)
                    break
    except Exception:
        pass

def addr_for_field(base_info, category, player_addr, offset, name):
    if category == "Body":
        app_off = int(base_info.get("Offset Appearance Data") or 0)
        n = (name or "").lower()
        if n.startswith("height") or n.startswith("wingspan"):
            return player_addr + app_off + offset
    return player_addr + offset

# -------------------------
# Batch Ops
# -------------------------

class BatchOps(tk.Toplevel):
    def __init__(self, master, app, base_mod):
        super().__init__(master)
        self.title("Batch Ops")
        self.geometry("900x600")
        self.app = app
        self.base = base_mod
        self.gm = app.gm
        self.base_info, self.categories = self.base.load_offsets()
        self.allowed_cats = [c for c in ("Attributes","Tendencies","Badges","Hotzones") if c in self.categories]

        top = ttk.Frame(self); top.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(top, text="Category:").pack(side=tk.LEFT)
        self.var_cat = tk.StringVar(value=self.allowed_cats[0] if self.allowed_cats else "")
        self.cmb_cat = ttk.Combobox(top, values=self.allowed_cats, textvariable=self.var_cat, state="readonly", width=16)
        self.cmb_cat.pack(side=tk.LEFT, padx=6)
        self.cmb_cat.bind("<<ComboboxSelected>>", lambda _e: self._reload_fields())

        ttk.Button(top, text="Export CSV (selected targets)", command=self.export_csv).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Import CSV → apply", command=self.import_csv).pack(side=tk.LEFT, padx=4)

        body = ttk.Frame(self); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        left = ttk.Frame(body); left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="Source Player").pack(anchor="w")
        frm_src = ttk.Frame(left); frm_src.pack(fill=tk.X, pady=(0,8))
        self.lst_src = tk.Listbox(frm_src, height=10, exportselection=False)
        vs_src = ttk.Scrollbar(frm_src, orient=tk.VERTICAL, command=self.lst_src.yview)
        self.lst_src.configure(yscrollcommand=vs_src.set)
        self.lst_src.pack(side=tk.LEFT, fill=tk.X, expand=True)
        vs_src.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(left, text="Target Players (multi-select)").pack(anchor="w")
        frm_tgt = ttk.Frame(left); frm_tgt.pack(fill=tk.BOTH, expand=True)
        self.lst_tgt = tk.Listbox(frm_tgt, height=18, selectmode=tk.EXTENDED, exportselection=False)
        vs_tgt = ttk.Scrollbar(frm_tgt, orient=tk.VERTICAL, command=self.lst_tgt.yview)
        self.lst_tgt.configure(yscrollcommand=vs_tgt.set)
        self.lst_tgt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vs_tgt.pack(side=tk.LEFT, fill=tk.Y)

        right = ttk.Frame(body); right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0))
        ttk.Label(right, text="Fields in Category").pack(anchor="w")
        frm_fields = ttk.Frame(right); frm_fields.pack(fill=tk.BOTH, expand=True)
        self.lst_fields = tk.Listbox(frm_fields, height=24, selectmode=tk.EXTENDED, exportselection=False)
        vs_fields = ttk.Scrollbar(frm_fields, orient=tk.VERTICAL, command=self.lst_fields.yview)
        self.lst_fields.configure(yscrollcommand=vs_fields.set)
        self.lst_fields.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vs_fields.pack(side=tk.LEFT, fill=tk.Y)
        self.chk_nonzero = tk.IntVar(value=0)
        ttk.Checkbutton(right, text="Only copy non-zero fields", variable=self.chk_nonzero).pack(anchor="w", pady=4)

        bottom = ttk.Frame(self); bottom.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(bottom, text="Copy from Source → Targets", command=self.copy_apply).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Refresh player list", command=self._reload_players).pack(side=tk.LEFT, padx=8)
        self.var_status = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.var_status).pack(side=tk.RIGHT)

        self._reload_players()
        self._reload_fields()

    def _reload_players(self):
        try:
            self.app._refresh_players()
        except Exception:
            pass
        self.lst_src.delete(0, tk.END)
        self.lst_tgt.delete(0, tk.END)
        self.players = list(self.app.players) if getattr(self.app, "players", None) else []
        for p in self.players:
            label = f"{p.index:04d} — {p.first} {p.last} ({p.team_name})"
            self.lst_src.insert(tk.END, label)
            self.lst_tgt.insert(tk.END, label)

    def _reload_fields(self):
        cat = self.var_cat.get()
        self.lst_fields.delete(0, tk.END)
        self.fields = list(self.categories.get(cat, []))
        for f in self.fields:
            name = str(f.get("name",""))
            off  = f.get("offset")
            sb   = f.get("startBit", 0)
            ln   = f.get("length")
            self.lst_fields.insert(tk.END, f"{name}  [off={hex(int(off)) if isinstance(off,int) else off}, sb={sb}, ln={ln}]")

    def _sel_index(self, listbox):
        try:
            idxs = listbox.curselection()
            return idxs[0] if idxs else None
        except Exception:
            return None

    def _sel_indices(self, listbox):
        try:
            return list(listbox.curselection())
        except Exception:
            return []

    def _player_by_row(self, row):
        return self.players[row] if row is not None and 0 <= row < len(self.players) else None

    def _field_rows(self):
        sel = self._sel_indices(self.lst_fields)
        return sel if sel else list(range(len(self.fields)))  # default: all

    def copy_apply(self):
        srow = self._sel_index(self.lst_src)
        trows = self._sel_indices(self.lst_tgt)
        if srow is None or not trows:
            messagebox.showwarning("Select players","Pick one source and at least one target."); return
        src = self._player_by_row(srow)
        tgts = [self._player_by_row(r) for r in trows if self._player_by_row(r)]
        fields_idx = self._field_rows()
        cat = self.var_cat.get()
        count = 0
        for fi in fields_idx:
            f = self.fields[fi]
            off = int(f["offset"]); sb = int(f.get("startBit", 0)); ln = int(f["length"])
            s_addr = src.addr + off
            raw_src = self.base.read_bits(self.gm, s_addr, 0, sb, ln)
            if self.chk_nonzero.get() and int(raw_src) == 0:
                continue
            for t in tgts:
                t_addr = t.addr + off
                ok = self.base.write_bits(self.gm, t_addr, 0, sb, ln, int(raw_src))
                count += 1 if ok else 0
        self.var_status.set(f"Applied {count} writes from {src.first} {src.last} to {len(tgts)} targets in '{cat}'.")

    def export_csv(self):
        trows = self._sel_indices(self.lst_tgt)
        if not trows:
            messagebox.showwarning("No targets","Select target players to export."); return
        players = [self._player_by_row(r) for r in trows if self._player_by_row(r)]
        cat = self.var_cat.get()
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
                                            initialfile=f"batch_{cat}.csv")
        if not path: return
        with open(path,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["PlayerIndex","First","Last","Category","Field","Offset","StartBit","Length","Raw"])
            fields = self.categories.get(cat, [])
            for p in players:
                for field in fields:
                    name = str(field["name"]); off = int(field["offset"]); sb = int(field.get("startBit",0)); ln = int(field["length"])
                    addr = p.addr + off
                    raw  = self.base.read_bits(self.gm, addr, 0, sb, ln)
                    w.writerow([p.index, p.first, p.last, cat, name, hex(off), sb, ln, raw])
        messagebox.showinfo("Exported", f"Wrote {path}")

    
def import_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path:
            return
        # Build strict name-only map
        pmap = {}
        for p in self.players:
            key = (_norm_name(p.first), _norm_name(p.last))
            pmap.setdefault(key, []).append(p)
        rows, writes, misses = 0, 0, 0
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                rows += 1
                try:
                    cat = row.get("Category","")
                    if cat not in self.categories:
                        continue
                    off_s = row.get("Offset","0")
                    off = int(off_s, 16) if str(off_s).lower().startswith("0x") else int(off_s)
                    sb  = int(row.get("StartBit","0") or 0)
                    ln  = int(row.get("Length","1") or 1)
                    raw = int(float(row.get("Raw","0") or 0))
                    fn  = _norm_name(row.get("First",""))
                    lnme = _norm_name(row.get("Last",""))
                    if not fn or not lnme:
                        misses += 1
                        continue
                    matches = pmap.get((fn, lnme), [])
                    if not matches:
                        misses += 1
                        continue
                    # If multiple exact name matches, apply to all
                    for p in matches:
                        addr = p.addr + off
                        ok = self.base.write_bits(self.gm, addr, 0, sb, ln, raw)
                        writes += 1 if ok else 0
                except Exception:
                    continue
        messagebox.showinfo("Imported", f"Read {rows} rows. Applied {writes} writes. Name misses: {misses}.")

        messagebox.showinfo("Imported", f"Read {rows} rows. Applied {writes} writes.")

# -------------------------
# Sort Players
# -------------------------

class SortPlayers(tk.Toplevel):
    def __init__(self, master, app, base_mod):
        super().__init__(master)
        self.title("Sort Players")
        self.geometry("700x360")
        self.app = app
        self.base = base_mod
        self.gm = app.gm
        self.base_info, self.categories = self.base.load_offsets()

        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Category:").grid(row=0, column=0, sticky="w")
        self.var_cat = tk.StringVar()
        cats = ["Players List"] + sorted(self.categories.keys())
        self.cmb_cat = ttk.Combobox(frm, values=cats, textvariable=self.var_cat, state="readonly", width=28)
        self.cmb_cat.grid(row=0, column=1, sticky="w", pady=2)
        self.var_cat.set("Players List")
        self.cmb_cat.bind("<<ComboboxSelected>>", lambda _e: self._reload_fields())

        ttk.Label(frm, text="Field:").grid(row=1, column=0, sticky="w")
        self.var_field = tk.StringVar()
        self.cmb_field = ttk.Combobox(frm, values=[], textvariable=self.var_field, state="readonly", width=28)
        self.cmb_field.grid(row=1, column=1, sticky="w", pady=2)

        self.var_desc = tk.IntVar(value=0)
        self.var_display = tk.IntVar(value=1)
        self.var_tiebreak = tk.StringVar(value="Index")
        ttk.Checkbutton(frm, text="Descending", variable=self.var_desc).grid(row=0, column=2, sticky="w", padx=(20,0))
        ttk.Checkbutton(frm, text="Use display mapping (Attributes 25–99, Tendencies 0–100, Badges=level)", variable=self.var_display).grid(row=1, column=2, sticky="w", padx=(20,0))

        ttk.Label(frm, text="Tie-breaker:").grid(row=2, column=0, sticky="w", pady=(8,2))
        self.cmb_tie = ttk.Combobox(frm, values=["Index","Last","First","Face ID","Team"], textvariable=self.var_tiebreak, state="readonly", width=16)
        self.cmb_tie.grid(row=2, column=1, sticky="w", pady=(8,2))

        ttk.Button(frm, text="Apply Sort", command=self.apply_sort).grid(row=3, column=0, sticky="w", pady=(12,0))
        ttk.Button(frm, text="Export values (csv)", command=self.export_values).grid(row=3, column=1, sticky="w", pady=(12,0), padx=(6,0))

        self._reload_fields()

    def _reload_fields(self):
        cat = self.var_cat.get()
        fields = []
        if cat == "Players List":
            fields = ["Index","First","Last","Face ID","Team"]
        else:
            fields = [str(f.get("name","")) for f in self.categories.get(cat, [])]
        self.cmb_field['values'] = fields
        if fields:
            self.var_field.set(fields[0])

    def _field_spec(self, cat, name):
        for f in self.categories.get(cat, []):
            if str(f.get("name","")).strip().lower() == name.strip().lower():
                try:
                    off = int(f["offset"]) if isinstance(f["offset"], int) else int(str(f["offset"]), 16) if str(f["offset"]).lower().startswith("0x") else int(str(f["offset"]))
                except Exception:
                    off = 0
                sb  = int(f.get("startBit", 0)); ln = int(f.get("length", 8))
                return off, sb, ln
        return None

    def _key_for_player(self, p, cat, field_name, disp_map: bool):
        if cat == "Players List":
            if field_name == "Index":   return (p.index, )
            if field_name == "First":   return (str(p.first).lower(), )
            if field_name == "Last":    return (str(p.last).lower(), )
            if field_name == "Face ID": return (int(p.face_id), )
            if field_name == "Team":    return (str(p.team_name).lower(), )
            return (0, )
        spec = self._field_spec(cat, field_name)
        if not spec:
            return (0, )
        off, sb, ln = spec
        addr = p.addr + off
        raw  = self.base.read_bits(self.gm, addr, 0, sb, ln)
        if not disp_map:
            return (int(raw), )
        if cat in ("Attributes","Durability"):
            try: return (int(self.base.raw_to_attr(int(raw), ln)), )
            except Exception: return (int(raw), )
        if cat == "Tendencies":
            try: return (int(self.base.raw_to_tendency(int(raw), ln)), )
            except Exception: return (int(raw), )
        if cat == "Badges":
            return (int(raw), )
        return (int(raw), )

    def _tie_for_player(self, p, tie_field):
        if tie_field == "Index":   return (p.index, )
        if tie_field == "Last":    return (str(p.last).lower(), )
        if tie_field == "First":   return (str(p.first).lower(), )
        if tie_field == "Face ID": return (int(p.face_id), )
        if tie_field == "Team":    return (str(p.team_name).lower(), )
        return (p.index, )

    def apply_sort(self):
        cat = self.var_cat.get(); fld = self.var_field.get()
        if not fld:
            messagebox.showwarning("Choose field","Select a field."); return
        disp = bool(self.var_display.get()); desc = bool(self.var_desc.get()); tie = self.var_tiebreak.get()
        items = []
        for p in self.app.players:
            k = self._key_for_player(p, cat, fld, disp)
            t = self._tie_for_player(p, tie)
            items.append((k, t, p))
        items.sort(key=lambda x: (x[0], x[1]), reverse=desc)
        self.app.players = [p for _,__,p in items]
        rebuild_player_tree(self.app)

    def export_values(self):
        cat = self.var_cat.get(); fld = self.var_field.get()
        if not fld:
            messagebox.showwarning("Choose field","Select a field."); return
        disp = bool(self.var_display.get())
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
                                            initialfile=f"sort_values_{cat}_{fld}.csv")
        if not path: return
        with open(path,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["Index","First","Last","Team",f"{cat}:{fld}","Mode"])
            for p in self.app.players:
                val = self._key_for_player(p, cat, fld, disp)[0]
                w.writerow([p.index, p.first, p.last, p.team_name, val, "display" if disp else "raw"])

# -------------------------
# Universal Filter Builder
# -------------------------

NUM_OPS = ["==","!=","<","<=",">",">=","between"]
BOOL_OPS = ["is","is not"]
STR_OPS  = ["contains","not contains","equals","starts with","ends with","regex"]

class FilterBuilder(tk.Toplevel):
    def __init__(self, master, app, base_mod):
        super().__init__(master)
        self.title("Filter Builder")
        self.geometry("840x560")
        self.app = app
        self.base = base_mod
        self.gm = app.gm
        self.base_info, self.categories = self.base.load_offsets()
        self.rules = []  # list of rule dicts
        self.result = []

        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Top controls
        top = ttk.Frame(frm); top.pack(fill=tk.X)
        ttk.Label(top, text="Combine:").pack(side=tk.LEFT)
        self.var_combine = tk.StringVar(value="AND")
        ttk.Combobox(top, values=["AND","OR"], textvariable=self.var_combine, state="readonly", width=6).pack(side=tk.LEFT, padx=(4,10))

        ttk.Button(top, text="Add Rule", command=self.add_rule).pack(side=tk.LEFT)
        ttk.Button(top, text="Edit", command=self.edit_rule).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Delete", command=self.delete_rule).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Clear", command=self.clear_rules).pack(side=tk.LEFT, padx=4)

        ttk.Button(top, text="Save Preset…", command=self.save_preset).pack(side=tk.RIGHT)
        ttk.Button(top, text="Load Preset…", command=self.load_preset).pack(side=tk.RIGHT, padx=(0,6))

        # Rules list
        mid = ttk.Frame(frm); mid.pack(fill=tk.BOTH, expand=True, pady=(8,8))
        frm_rules = ttk.Frame(mid); frm_rules.pack(fill=tk.BOTH, expand=True)
        self.lst = tk.Listbox(frm_rules, height=12)
        vs_rules = ttk.Scrollbar(frm_rules, orient=tk.VERTICAL, command=self.lst.yview)
        self.lst.configure(yscrollcommand=vs_rules.set)
        self.lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vs_rules.pack(side=tk.LEFT, fill=tk.Y)

        # Bottom actions + preview
        bot = ttk.Frame(frm); bot.pack(fill=tk.X)
        ttk.Button(bot, text="Build List", command=self.build_list).pack(side=tk.LEFT)
        ttk.Button(bot, text="Show in Players", command=self.show_in_players).pack(side=tk.LEFT, padx=6)
        ttk.Button(bot, text="Export CSV", command=self.export_list).pack(side=tk.LEFT, padx=6)
        ttk.Button(bot, text="Restore Full Roster", command=self.restore_players).pack(side=tk.LEFT, padx=6)

        self.var_status = tk.StringVar(value="0 rules.")
        ttk.Label(bot, textvariable=self.var_status).pack(side=tk.RIGHT)

    # ----- Rule Editing -----

    def _cats(self):
        return ["Players List"] + list(self.categories.keys())

    def add_rule(self):
        RuleEditor(self, self.base, self.categories, on_ok=self._add_rule_obj)

    def edit_rule(self):
        sel = self._sel_index()
        if sel is None: return
        RuleEditor(self, self.base, self.categories, existing=self.rules[sel], on_ok=lambda r: self._edit_rule_obj(sel, r))

    def delete_rule(self):
        sel = self._sel_index()
        if sel is None: return
        self.rules.pop(sel)
        self._refresh_rules()

    def clear_rules(self):
        self.rules.clear()
        self._refresh_rules()

    def _sel_index(self):
        try:
            c = self.lst.curselection()
            return c[0] if c else None
        except Exception:
            return None

    def _add_rule_obj(self, rule):
        self.rules.append(rule); self._refresh_rules()

    def _edit_rule_obj(self, idx, rule):
        self.rules[idx] = rule; self._refresh_rules()

    def _refresh_rules(self):
        self.lst.delete(0, tk.END)
        for r in self.rules:
            self.lst.insert(tk.END, self._rule_text(r))
        self.var_status.set(f"{len(self.rules)} rules.")

    def _rule_text(self, r):
        cat, field, op, val, disp, case = r["cat"], r["field"], r["op"], r["val"], r.get("disp",False), r.get("case",False)
        val_txt = f"{val}" if not isinstance(val, (list,tuple)) else f"{val[0]}..{val[1]}"
        extras = []
        if disp and cat in ("Attributes","Tendencies"): extras.append("display")
        if case and cat == "Players List": extras.append("case-ins")
        extra = f" ({', '.join(extras)})" if extras else ""
        return f"{cat} / {field} {op} {val_txt}{extra}"

    # ----- Evaluate -----

    def build_list(self):
        self.result = []
        combine_and = (self.var_combine.get() == "AND")
        players = list(self.app.players)
        for p in players:
            verdicts = []
            for r in self.rules:
                ok = self._check_rule(p, r)
                verdicts.append(bool(ok))
            passed = (all(verdicts) if combine_and else any(verdicts)) if self.rules else True
            if passed:
                self.result.append(p)
        self.var_status.set(f"Built {len(self.result)} players.")

    def show_in_players(self):
        if not self.result:
            self.build_list()
        if not self.result:
            messagebox.showinfo("Empty","No players matched the filters."); return
        if not hasattr(self.app, "_players_backup"):
            self.app._players_backup = list(self.app.players)
        self.app.players = list(self.result)
        rebuild_player_tree(self.app)

    def restore_players(self):
        if hasattr(self.app, "_players_backup"):
            self.app.players = list(self.app._players_backup)
            delattr(self.app, "_players_backup")
            rebuild_player_tree(self.app)

    def export_list(self):
        if not self.result:
            self.build_list()
        if not self.result:
            messagebox.showinfo("Empty","No players matched the filters."); return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
                                            initialfile="filtered_players.csv")
        if not path: return
        with open(path,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["Index","First","Last","Team","FaceID"])
            for p in self.result:
                w.writerow([p.index, p.first, p.last, p.team_name, p.face_id])
        messagebox.showinfo("Exported", f"Wrote {path}")

    # ----- Rule evaluation helpers -----

    def _field_spec(self, cat, field):
        for f in self.categories.get(cat, []):
            if str(f.get("name","")).strip().lower() == field.strip().lower():
                try:
                    off = int(f["offset"]) if isinstance(f["offset"], int) else int(str(f["offset"]), 16) if str(f["offset"]).lower().startswith("0x") else int(str(f["offset"]))
                except Exception:
                    off = 0
                sb  = int(f.get("startBit", 0)); ln = int(f.get("length", 8))
                return off, sb, ln, f
        return None

    def _read_numeric(self, p, cat, field, disp=False):
        spec = self._field_spec(cat, field)
        if not spec:
            return None
        off, sb, ln, meta = spec
        addr = addr_for_field(self.base_info, cat, p.addr, off, field)
        raw  = int(self.base.read_bits(self.gm, addr, 0, sb, ln))
        if disp and cat in ("Attributes","Durability"):
            try: return int(self.base.raw_to_attr(raw, ln))
            except Exception: return raw
        if disp and cat == "Tendencies":
            try: return int(self.base.raw_to_tendency(raw, ln))
            except Exception: return raw
        return raw

    def _value_of(self, p, cat, field, disp=False):
        if cat == "Players List":
            if field == "Index": return p.index
            if field == "First": return str(p.first)
            if field == "Last":  return str(p.last)
            if field == "Team":  return str(p.team_name)
            if field == "Face ID": return int(p.face_id)
            return None
        # Non built-ins: treat numeric
        return self._read_numeric(p, cat, field, disp=disp)

    def _check_rule(self, p, r):
        cat, field, op, val = r["cat"], r["field"], r["op"], r["val"]
        disp = bool(r.get("disp", False))
        case = bool(r.get("case", False))
        v = self._value_of(p, cat, field, disp=disp)

        # String operations only for built-ins we expose as strings
        if isinstance(v, str):
            t = v if case else v.lower()
            s = str(val)
            s = s if case else s.lower()
            if op == "contains":      return s in t
            if op == "not contains":  return s not in t
            if op == "equals":        return t == s
            if op == "starts with":   return t.startswith(s)
            if op == "ends with":     return t.endswith(s)
            if op == "regex":
                try: return re.search(val, v) is not None
                except Exception: return False
            return False

        # Numeric / boolean
        try:
            iv = int(v) if v is not None else None
        except Exception:
            return False
        if iv is None:
            return False

        if op in ("is","is not"):
            target = 1 if str(val).strip().lower() in ("1","true","yes","on") else 0
            return (iv == target) if op == "is" else (iv != target)

        try:
            if op == "between":
                a,b = val if isinstance(val,(list,tuple)) else (val, val)
                a = float(a); b = float(b)
                return a <= float(iv) <= b
            target = float(val)
            if op == "==": return iv == target
            if op == "!=": return iv != target
            if op == "<":  return iv <  target
            if op == "<=": return iv <= target
            if op == ">":  return iv >  target
            if op == ">=": return iv >= target
        except Exception:
            return False
        return False

    # ----- Presets -----

    def save_preset(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")],
                                            initialfile="filters_preset.json")
        if not path: return
        preset = {"combine": self.var_combine.get(), "rules": self.rules}
        try:
            with open(path,"w",encoding="utf-8") as f:
                json.dump(preset, f, indent=2)
            messagebox.showinfo("Saved", f"Preset saved to {path}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def load_preset(self):
        path = filedialog.askopenfilename(filetypes=[("JSON","*.json"),("All","*.*")])
        if not path: return
        try:
            preset = json.load(open(path,"r",encoding="utf-8"))
            self.var_combine.set(str(preset.get("combine","AND")).upper())
            self.rules = list(preset.get("rules", []))
            self._refresh_rules()
        except Exception as e:
            messagebox.showerror("Load error", str(e))

# ----- Rule Editor window -----

class RuleEditor(tk.Toplevel):
    def __init__(self, master, base_mod, categories, existing=None, on_ok=None):
        super().__init__(master)
        self.title("Rule")
        self.geometry("720x240")
        self.base = base_mod
        self.categories = categories
        self.on_ok = on_ok

        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Category:").grid(row=0, column=0, sticky="w")
        self.var_cat = tk.StringVar()
        self.cmb_cat = ttk.Combobox(frm, values=["Players List"] + list(categories.keys()), textvariable=self.var_cat, state="readonly", width=28)
        self.cmb_cat.grid(row=0, column=1, sticky="w")
        self.cmb_cat.bind("<<ComboboxSelected>>", lambda _e: self._reload_fields())

        ttk.Label(frm, text="Field:").grid(row=1, column=0, sticky="w")
        self.var_field = tk.StringVar()
        self.cmb_field = ttk.Combobox(frm, values=[], textvariable=self.var_field, state="readonly", width=28)
        self.cmb_field.grid(row=1, column=1, sticky="w")
        self.cmb_field.bind("<<ComboboxSelected>>", lambda _e: self._reload_ops())

        ttk.Label(frm, text="Operator:").grid(row=2, column=0, sticky="w")
        self.var_op = tk.StringVar()
        self.cmb_op = ttk.Combobox(frm, values=[], textvariable=self.var_op, state="readonly", width=18)
        self.cmb_op.grid(row=2, column=1, sticky="w")

        ttk.Label(frm, text="Value:").grid(row=3, column=0, sticky="w")
        self.var_val = tk.StringVar()
        self.ent_val = ttk.Entry(frm, textvariable=self.var_val, width=26)
        self.ent_val.grid(row=3, column=1, sticky="w")

        ttk.Label(frm, text="Value 2 (between):").grid(row=4, column=0, sticky="w")
        self.var_val2 = tk.StringVar()
        self.ent_val2 = ttk.Entry(frm, textvariable=self.var_val2, width=26)
        self.ent_val2.grid(row=4, column=1, sticky="w")

        self.var_disp = tk.IntVar(value=0)
        self.var_case = tk.IntVar(value=0)
        ttk.Checkbutton(frm, text="Use display mapping (Attributes/Tendencies)", variable=self.var_disp).grid(row=5, column=1, sticky="w", pady=(8,0))
        ttk.Checkbutton(frm, text="Case sensitive (string ops)", variable=self.var_case).grid(row=6, column=1, sticky="w")

        btns = ttk.Frame(frm); btns.grid(row=7, column=0, columnspan=2, sticky="w", pady=(10,0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side=tk.LEFT)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=6)

        # Preload
        if existing:
            self.var_cat.set(existing["cat"]); self._reload_fields()
            self.var_field.set(existing["field"]); self._reload_ops()
            self.var_op.set(existing["op"])
            if isinstance(existing["val"], (list,tuple)):
                self.var_val.set(str(existing["val"][0])); self.var_val2.set(str(existing["val"][1]))
            else:
                self.var_val.set(str(existing["val"])); self.var_val2.set("")
            self.var_disp.set(1 if existing.get("disp", False) else 0)
            self.var_case.set(1 if existing.get("case", False) else 0)
        else:
            self.var_cat.set("Players List"); self._reload_fields()

    def _reload_fields(self):
        cat = self.var_cat.get()
        if cat == "Players List":
            fields = ["Index","First","Last","Team","Face ID"]
        else:
            fields = [str(f.get("name","")) for f in self.categories.get(cat, [])]
        self.cmb_field['values'] = fields
        if fields:
            self.var_field.set(fields[0])
        self._reload_ops()

    def _field_spec(self, cat, name):
        for f in self.categories.get(cat, []):
            if str(f.get("name","")).strip().lower() == name.strip().lower():
                try:
                    off = int(f["offset"]) if isinstance(f["offset"], int) else int(str(f["offset"]), 16) if str(f["offset"]).lower().startswith("0x") else int(str(f["offset"]))
                except Exception:
                    off = 0
                sb  = int(f.get("startBit", 0)); ln = int(f.get("length", 8))
                return off, sb, ln, f
        return None

    def _reload_ops(self):
        cat = self.var_cat.get(); field = self.var_field.get()
        ops = NUM_OPS
        if cat == "Players List":
            if field in ("Index","Face ID"): ops = NUM_OPS
            else: ops = STR_OPS
        else:
            # Infer boolean if Badges length=1
            spec = self._field_spec(cat, field)
            if spec:
                _, _, ln, _ = spec
                if cat == "Badges" and ln == 1:
                    ops = BOOL_OPS
                else:
                    ops = NUM_OPS
        self.cmb_op['values'] = ops
        if ops:
            self.var_op.set(ops[0])

    def _ok(self):
        cat, field, op = self.var_cat.get(), self.var_field.get(), self.var_op.get()
        if not field or not op:
            self.destroy(); return
        val = self.var_val.get().strip()
        if op == "between":
            val2 = self.var_val2.get().strip()
            if not val2: val2 = val
            out_val = [val, val2]
        else:
            out_val = val
        rule = {
            "cat": cat,
            "field": field,
            "op": op,
            "val": out_val,
            "disp": bool(self.var_disp.get()),
            "case": bool(self.var_case.get()),
        }
        if callable(self.on_ok):
            self.on_ok(rule)
        self.destroy()

# -------------------------
# Tools Menubar
# -------------------------

class ToolsMenu:
    def __init__(self, app, base_mod, undo_mgr):
        self.app = app
        self.base = base_mod
        self.undo = undo_mgr
        self._draft_defaults = [
            {"cat":"Players List","field":"Team","op":"contains","val":"draft","disp":False,"case":False},
        ]
        self._filter_win = None  # lazy
        self._draft_last_built = []

        menubar = tk.Menu(app)
        tools = tk.Menu(menubar, tearoff=0)

        # Undo/Redo
        tools.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo_action)
        tools.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo_action)
        tools.add_separator()

        # Windows
        tools.add_command(label="Batch Ops…", command=self.open_batch)
        tools.add_command(label="Sort Players…", command=self.open_sort)
        tools.add_command(label="Filter Builder…", command=self.open_filters)
        _tools_add_import_export(tools, self)


        # Draft quick
        draft = tk.Menu(tools, tearoff=0)
        draft.add_command(label="Quick Build (Prospects undrafted)", command=self.draft_quick_build)
        draft.add_command(label="Show in Players", command=self.draft_show)
        draft.add_command(label="Export CSV", command=self.draft_export)
        draft.add_command(label="Restore Full Roster", command=self.draft_restore)
        tools.add_cascade(label="Draft Class", menu=draft)

        menubar.add_cascade(label="Tools", menu=tools)
        app.config(menu=menubar)

        # Key bindings
        try:
            app.bind_all("<Control-z>", lambda e: self.undo_action())
            app.bind_all("<Control-y>", lambda e: self.redo_action())
        except Exception:
            pass

    # Actions
    def undo_action(self):
        self.undo.undo()

    def redo_action(self):
        self.undo.redo()

    def open_batch(self):
        BatchOps(self.app, self.app, self.base)

    def open_sort(self):
        SortPlayers(self.app, self.app, self.base)

    def _get_filter_win(self):
        if self._filter_win is None or not self._filter_win.winfo_exists():
            self._filter_win = FilterBuilder(self.app, self.app, self.base)
        return self._filter_win

    def open_filters(self):
        self._get_filter_win().deiconify()
    # --- Master CSV handlers ---
    def import_master_match(self):
        path = filedialog.askopenfilename(title="Select Master CSV", filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path: return
        imp = MasterCSV(self.app, self.base)
        logp = os.path.join(os.path.dirname(path), "import_log.txt")
        writes, unmatched, skipped = imp.import_csv(path, mode="match_names", skip_blank=True, log_path=logp)
        messagebox.showinfo("Import done", f"Writes: {writes}\nUnmatched rows: {unmatched}\nBlanks skipped: {skipped}\nLog: {logp}")

    def import_master_order(self):
        path = filedialog.askopenfilename(title="Select Master CSV", filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path: return
        imp = MasterCSV(self.app, self.base)
        logp = os.path.join(os.path.dirname(path), "import_log.txt")
        writes, unmatched, skipped = imp.import_csv(path, mode="by_order", skip_blank=True, log_path=logp)
        messagebox.showinfo("Import done", f"Writes: {writes}\nUnmatched rows: {unmatched}\nBlanks skipped: {skipped}\nLog: {logp}")

    def export_master_selected(self):
        sel = self.app.tree.selection() if hasattr(self.app, "tree") else []
        path = filedialog.asksaveasfilename(title="Save Master CSV", defaultextension=".csv", filetypes=[("CSV","*.csv")], initialfile="Players_Master.csv")
        if not path: return
        imp = MasterCSV(self.app, self.base)
        ok = imp.export_csv(path, template_csv=None, selection_only=bool(sel))
        if ok:
            messagebox.showinfo("Exported", f"Wrote {path}")
        else:
            messagebox.showerror("Export failed","Could not export.")

        self._filter_win.lift()

    # Draft helpers using FilterBuilder under the hood
    def draft_quick_build(self):
        fb = self._get_filter_win()
        # default rules: Is Draft Prospect == 1 AND Was Drafted == 0
        # If fields exist in offsets.json, add corresponding numeric rules; else rely on team name 'draft' as fallback.
        rules = []
        # Attempt to find fields
        def has_field(cat, name):
            return any(str(f.get("name","")).strip().lower() == name.lower()
                       for cat, arr in fb.categories.items() for f in arr if cat)
        # We cannot know category names; search across categories for matching names
        cand = [("Is Draft Prospect","==","1"),
                ("Was Drafted","==","0")]
        added_named = False
        for nm,op,val in cand:
            for cat, arr in fb.categories.items():
                for f in arr:
                    if str(f.get("name","")).strip().lower() == nm.lower():
                        rules.append({"cat":cat,"field":nm,"op":op,"val":val,"disp":False,"case":False})
                        added_named = True
                        break
        if not added_named:
            rules.extend(self._draft_defaults)
        fb.rules = rules
        fb.var_combine.set("AND")
        fb._refresh_rules()
        fb.build_list()
        self._draft_last_built = list(fb.result)

    def draft_show(self):
        fb = self._get_filter_win()
        if not fb.result:
            self.draft_quick_build()
        fb.show_in_players()

    def draft_export(self):
        fb = self._get_filter_win()
        if not fb.result:
            self.draft_quick_build()
        fb.export_list()

    def draft_restore(self):
        fb = self._get_filter_win()
        fb.restore_players()

# -------------------------
# CSV Import/Export (Master headers)
# -------------------------

class MasterCSV:
    """Importer/Exporter that honors user CSV headers. Skips blanks. Two modes: match by First+Last or apply by order."""
    def __init__(self, app, base_mod, offsets_template_csv=None):
        self.app = app
        self.base = base_mod
        self.gm = app.gm
        self.base_info, self.categories = self.base.load_offsets()
        # Build name->(category, spec)
        self.field_map = {}
        for cat, fields in self.categories.items():
            for f in fields:
                name = str(f.get("name","")).strip()
                if not name: 
                    continue
                # last one wins, but record duplicates
                key = name.lower()
                if key in self.field_map and self.field_map[key][0] != cat:
                    # ambiguous; prefer previously stored; leave as-is
                    pass
                else:
                    self.field_map[key] = (cat, f)
        # Special: support First Name and Last Name if present as writable strings via Base offsets
        base_info = self.base_info or {}
        self.first_off = int(base_info.get("Offset First Name", "0") , 16) if isinstance(base_info.get("Offset First Name"), str) else int(base_info.get("Offset First Name") or 0)
        self.last_off  = int(base_info.get("Offset Last Name",  "0") , 16) if isinstance(base_info.get("Offset Last Name"),  str) else int(base_info.get("Offset Last Name")  or 0)
        self.appearance_off = int(base_info.get("Offset Appearance Data","0"),16) if isinstance(base_info.get("Offset Appearance Data"), str) else int(base_info.get("Offset Appearance Data") or 0)

    def _addr_for_field(self, category, p_addr, off, name):
        # Mirror helper logic in this file
        if category == "Body":
            n = (name or "").lower()
            if n.startswith("height") or n.startswith("wingspan"):
                return p_addr + self.appearance_off + off
        return p_addr + off

    def _parse_int(self, s):
        if s is None: return None
        s = str(s).strip()
        if s == "": return None
        # hex like 0x1A
        try:
            if s.lower().startswith("0x"):
                return int(s,16)
            return int(float(s))
        except:
            return None

    def _parse_float_bits(self, s):
        import struct
        if s is None or str(s).strip() == "": 
            return None
        try:
            fval = float(s)
        except:
            return None
        # little-endian IEEE-754
        return struct.unpack("<I", struct.pack("<f", fval))[0]

    def import_csv(self, path, mode="match_names", skip_blank=True, log_path=None):
        """
        mode: 'match_names' or 'by_order'
        skip_blank: True to keep existing values when CSV empty
        """
        import csv, time
        if not path: 
            return (0,0,0)
        players = list(getattr(self.app, "players", []))
        if not players:
            messagebox.showerror("No players","Player list not loaded."); 
            return (0,0,0)
        # Build matching index
        name_index = {f"{p.first} {p.last}".strip().lower(): p for p in players}
        # Read CSV
        rows = []
        with open(path,"r",encoding="utf-8") as f:
            r = csv.DictReader(f)
            headers = r.fieldnames or []
            for row in r:
                rows.append(row)
        total_rows = len(rows)
        writes = 0
        skipped_blank = 0
        unmatched = 0
        issues = []
        for i,row in enumerate(rows):
            # resolve target player
            target = None
            if mode == "match_names":
                fn = str(row.get("First Name","") or "").strip()
                ln = str(row.get("Last Name","") or "").strip()
                key = f"{fn} {ln}".strip().lower()
                target = name_index.get(key)
                if not target:
                    unmatched += 1
                    issues.append(f"Row {i+2}: no match for '{fn} {ln}'")
                    continue
            else:
                if i < len(players):
                    target = players[i]
                else:
                    unmatched += 1
                    issues.append(f"Row {i+2}: no player at position {i} for by_order mode")
                    continue
            # apply fields
            for h, val in row.items():
                if h is None:
                    continue
                name_key = h.strip().lower()
                # skip control headers and identity headers
                if name_key in ("first name","last name","playerindex","index","team","team name"):
                    continue
                if skip_blank and (val is None or str(val).strip() == ""):
                    skipped_blank += 1
                    continue
                if name_key not in self.field_map:
                    # header not recognized; ignore silently
                    continue
                cat, spec = self.field_map[name_key]
                off = int(spec.get("offset", 0), 16) if isinstance(spec.get("offset"), str) else int(spec.get("offset") or 0)
                sb  = int(spec.get("startBit", 0) or 0)
                ln  = int(spec.get("length",   0) or 0)
                addr = self._addr_for_field(cat, target.addr, off, h)
                # Decide write value
                if (h.strip().lower() == "weight") and ln == 32 and sb == 0:
                    new_raw = self._parse_float_bits(val)
                    if new_raw is None:
                        continue
                    ok = self.base.write_bits(self.app.gm, addr, 0, 0, 32, int(new_raw))
                    writes += 1 if ok else 0
                    continue
                # default numeric
                ival = self._parse_int(val)
                if ival is None:
                    # leave unchanged
                    continue
                ok = self.base.write_bits(self.app.gm, addr, 0, sb, ln if ln else 8, int(ival))
                writes += 1 if ok else 0
        # optional log
        if log_path:
            try:
                with open(log_path,"w",encoding="utf-8") as f:
                    f.write(f"Imported: {writes} writes over {total_rows} rows. Unmatched rows: {unmatched}. Blanks skipped: {skipped_blank}.\n")
                    for line in issues:
                        f.write(line+"\n")
            except Exception:
                pass
        rebuild_player_tree(self.app)
        return (writes, unmatched, skipped_blank)

    def export_csv(self, path, template_csv=None, selection_only=False):
        """Export using template headers if provided, else all known field names sorted by category then name."""
        import csv
        players = list(getattr(self.app, "players", []))
        if selection_only:
            try:
                tree = self.app.tree
                sel = tree.selection()
                idxs = set(int(self.app.tree.item(i,"values")[0]) for i in sel)
                players = [p for p in players if int(p.index) in idxs]
            except Exception:
                pass
        if not players:
            messagebox.showwarning("No players","Nothing to export.")
            return False
        headers = []
        if template_csv:
            try:
                with open(template_csv,"r",encoding="utf-8") as f:
                    r = csv.reader(f)
                    headers = next(r)
            except Exception:
                headers = []
        if not headers:
            # default header list: identity + all mapped fields
            headers = ["First Name","Last Name"]
            names = sorted(set(k for k in self.field_map.keys()))
            headers.extend([n for n in (name.title() for name in names)])  # title-case for aesthetics
            # Better: use original keys exactly; keep as stored
            headers = ["First Name","Last Name"] + sorted([k for k in self.field_map.keys()])
        with open(path,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            for p in players:
                row = []
                # build a dict of readable values
                values = {}
                for key,(cat,spec) in self.field_map.items():
                    off = int(spec.get("offset", 0), 16) if isinstance(spec.get("offset"), str) else int(spec.get("offset") or 0)
                    sb  = int(spec.get("startBit", 0) or 0)
                    ln  = int(spec.get("length",   0) or 0)
                    addr = self._addr_for_field(cat, p.addr, off, key)
                    raw  = self.base.read_bits(self.app.gm, addr, 0, sb, ln if ln else 8)
                    values[key] = raw
                # emit row in header order
                for h in headers:
                    lk = h.strip().lower()
                    if lk == "first name":
                        row.append(p.first)
                    elif lk == "last name":
                        row.append(p.last)
                    elif lk in values:
                        row.append(values[lk])
                    else:
                        row.append("")
                w.writerow(row)
        return True


def _tools_add_import_export(menu, host):
    """Extend Tools menu with CSV import/export items using MasterCSV."""
    menu.add_separator()
    menu.add_command(label="Import CSV (match First+Last)", command=host.import_master_match)
    menu.add_command(label="Import CSV (apply by roster order)", command=host.import_master_order)
    menu.add_command(label="Export CSV (selected → Master headers)", command=host.export_master_selected)

# -------------------------
# Launch base app with menu
# -------------------------

def main():
    if sys.platform != "win32":
        print("Windows-only."); return
    app = base.App()
    ToolsMenu(app, base, undo_manager)  # attach menubar to main window
    app.mainloop()

if __name__ == "__main__":
    main()
