
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor — Stage 5.3 (Menu Tools)
Windows-only. Requires an existing editor script (Stage 3.x or Stage 4) and offsets.json.

Changes vs 5.2:
- Removes the separate "Stage 5 — Tools" window.
- Adds a top menubar "Tools" with:
    Undo, Redo, Batch Ops…, Sort Players…, Draft Class ▶ Filters…, Build List, Show in Players, Export CSV, Restore Full Roster.
- "Filters…" opens the Draft Class Builder window. Others run directly.
"""

import os, sys, csv, json, importlib.util, tkinter as tk
from tkinter import ttk, filedialog, messagebox

# -------------------------
# Locate and load base editor
# -------------------------

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

for sym in ("GameMemory","read_bits","write_bits","resolve_table_base","PLAYER_STRIDE","PLAYER_PTR_CHAINS","load_offsets"):
    if not hasattr(base, sym):
        print(f"Missing symbol in base editor: {sym}")
        sys.exit(1)

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
        self.lst_src = tk.Listbox(left, height=10, exportselection=False)
        self.lst_src.pack(fill=tk.X, pady=(0,8))
        ttk.Label(left, text="Target Players (multi-select)").pack(anchor="w")
        self.lst_tgt = tk.Listbox(left, height=18, selectmode=tk.EXTENDED, exportselection=False)
        self.lst_tgt.pack(fill=tk.BOTH, expand=True)

        right = ttk.Frame(body); right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0))
        ttk.Label(right, text="Fields in Category").pack(anchor="w")
        self.lst_fields = tk.Listbox(right, height=24, selectmode=tk.EXTENDED, exportselection=False)
        self.lst_fields.pack(fill=tk.BOTH, expand=True)
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
        if not path: return
        idx_map = {int(p.index): p for p in self.players}
        name_map = {f"{p.first} {p.last}".lower(): p for p in self.players}
        rows, writes = 0, 0
        with open(path,"r",encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                rows += 1
                try:
                    cat = row.get("Category","")
                    if cat not in self.categories:
                        continue
                    name = row.get("Field",""); off = row.get("Offset","0")
                    off = int(off, 16) if str(off).lower().startswith("0x") else int(off)
                    sb  = int(row.get("StartBit","0")); ln = int(row.get("Length","1")); raw = int(float(row.get("Raw","0")))
                    p = None
                    if row.get("PlayerIndex",""):
                        try: p = idx_map.get(int(row["PlayerIndex"]))
                        except Exception: p = None
                    if p is None and row.get("First") and row.get("Last"):
                        p = name_map.get(f'{row["First"]} {row["Last"]}'.lower())
                    if p is None:
                        continue
                    addr = p.addr + off
                    ok = self.base.write_bits(self.gm, addr, 0, sb, ln, raw)
                    writes += 1 if ok else 0
                except Exception:
                    continue
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
# Draft Class Builder
# -------------------------

class DraftClassTool(tk.Toplevel):
    def __init__(self, master, app, base_mod):
        super().__init__(master)
        self.title("Draft Class — Filters")
        self.geometry("720x420")
        self.app = app
        self.base = base_mod
        self.gm = app.gm
        self.base_info, self.categories = self.base.load_offsets()

        # Fallback known specs if not present in offsets.json
        self.SPEC_IS_PROSPECT = self._spec_from_json("Is Draft Prospect") or (0x105, 1, 1)
        self.SPEC_WAS_DRAFTED = self._spec_from_json("Was Drafted") or (0x105, 2, 1)
        self.SPEC_ROUND_DRAFT = self._spec_from_json("Round Drafted") or (0x2E3, 3, 4)
        self.SPEC_PICK_NUM    = self._spec_from_json("Draft Pick Number") or (0x2F2, 6, 6)

        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Filters (AND):").grid(row=0, column=0, sticky="w")
        self.var_f_prospect = tk.IntVar(value=1)
        self.var_f_notdraft = tk.IntVar(value=1)
        self.var_f_round0   = tk.IntVar(value=0)
        self.var_f_pick0    = tk.IntVar(value=0)
        self.var_f_teamname = tk.IntVar(value=0)
        ttk.Checkbutton(frm, text="Is Draft Prospect == 1", variable=self.var_f_prospect).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Was Drafted == 0", variable=self.var_f_notdraft).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Round Drafted == 0", variable=self.var_f_round0).grid(row=3, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Draft Pick Number == 0", variable=self.var_f_pick0).grid(row=4, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Team name contains 'draft'", variable=self.var_f_teamname).grid(row=5, column=0, sticky="w")

        ttk.Button(frm, text="Build List", command=self.build_list).grid(row=6, column=0, sticky="w", pady=(10,0))
        ttk.Button(frm, text="Show in Players tab", command=self.show_in_players).grid(row=6, column=1, sticky="w", pady=(10,0), padx=(8,0))
        ttk.Button(frm, text="Export CSV", command=self.export_list).grid(row=6, column=2, sticky="w", pady=(10,0), padx=(8,0))
        ttk.Button(frm, text="Restore Full Roster", command=self.restore_players).grid(row=6, column=3, sticky="w", pady=(10,0), padx=(8,0))

        ttk.Label(frm, text="Draft Class (preview):").grid(row=7, column=0, sticky="w", pady=(12,2))
        self.lst = tk.Listbox(frm, height=12)
        self.lst.grid(row=8, column=0, columnspan=4, sticky="nsew")
        frm.rowconfigure(8, weight=1)
        frm.columnconfigure(3, weight=1)

        self.current_list = []
        self._built_once = False

    def _spec_from_json(self, field_name):
        for cat, fields in self.categories.items():
            for f in fields:
                if str(f.get("name","")).strip().lower() == field_name.lower():
                    try:
                        off = int(f["offset"]) if isinstance(f["offset"], int) else int(str(f["offset"]), 16) if str(f["offset"]).lower().startswith("0x") else int(str(f["offset"]))
                    except Exception:
                        off = 0
                    sb  = int(f.get("startBit", 0)); ln = int(f.get("length", 1))
                    return (off, sb, ln)
        return None

    def _read_field(self, p, spec):
        off, sb, ln = spec
        addr = p.addr + off
        return int(self.base.read_bits(self.gm, addr, 0, sb, ln))

    def build_list(self):
        self.current_list = []
        players = list(self.app.players)
        for p in players:
            ok = True
            if self.var_f_prospect.get():
                if self._read_field(p, self.SPEC_IS_PROSPECT) != 1:
                    ok = False
            if ok and self.var_f_notdraft.get():
                if self._read_field(p, self.SPEC_WAS_DRAFTED) != 0:
                    ok = False
            if ok and self.var_f_round0.get():
                if self._read_field(p, self.SPEC_ROUND_DRAFT) != 0:
                    ok = False
            if ok and self.var_f_pick0.get():
                if self._read_field(p, self.SPEC_PICK_NUM) != 0:
                    ok = False
            if ok and self.var_f_teamname.get():
                if "draft" not in str(p.team_name).lower():
                    ok = False
            if ok:
                self.current_list.append(p)
        self.lst.delete(0, tk.END)
        for p in self.current_list:
            self.lst.insert(tk.END, f"{p.index:04d} — {p.first} {p.last} ({p.team_name})")
        self._built_once = True

    def show_in_players(self):
        if not self._built_once:
            self.build_list()
        if not self.current_list:
            messagebox.showinfo("Empty","No players matched the filters."); return
        if not hasattr(self.app, "_players_backup"):
            self.app._players_backup = list(self.app.players)
        self.app.players = list(self.current_list)
        rebuild_player_tree(self.app)

    def restore_players(self):
        if hasattr(self.app, "_players_backup"):
            self.app.players = list(self.app._players_backup)
            delattr(self.app, "_players_backup")
            rebuild_player_tree(self.app)

    def export_list(self):
        if not self._built_once:
            self.build_list()
        if not self.current_list:
            messagebox.showinfo("Empty","No players matched the filters."); return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
                                            initialfile="draft_class_list.csv")
        if not path: return
        with open(path,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["Index","First","Last","Team","FaceID"])
            for p in self.current_list:
                w.writerow([p.index, p.first, p.last, p.team_name, p.face_id])
        messagebox.showinfo("Exported", f"Wrote {path}")

# -------------------------
# Tools Menubar
# -------------------------

class ToolsMenu:
    def __init__(self, app, base_mod, undo_mgr):
        self.app = app
        self.base = base_mod
        self.undo = undo_mgr
        self._draft_tool = None  # lazy

        menubar = tk.Menu(app)
        tools = tk.Menu(menubar, tearoff=0)

        # Undo/Redo
        tools.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo_action)
        tools.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo_action)
        tools.add_separator()

        # Windows
        tools.add_command(label="Batch Ops…", command=self.open_batch)
        tools.add_command(label="Sort Players…", command=self.open_sort)

        # Draft submenu
        draft = tk.Menu(tools, tearoff=0)
        draft.add_command(label="Filters…", command=self.open_draft_filters)
        draft.add_command(label="Build List", command=self.draft_build)
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

    def _get_draft_tool(self):
        if self._draft_tool is None or not self._draft_tool.winfo_exists():
            self._draft_tool = DraftClassTool(self.app, self.app, self.base)
        return self._draft_tool

    def open_draft_filters(self):
        self._get_draft_tool().deiconify()
        self._draft_tool.lift()

    def draft_build(self):
        self._get_draft_tool().build_list()

    def draft_show(self):
        self._get_draft_tool().show_in_players()

    def draft_export(self):
        self._get_draft_tool().export_list()

    def draft_restore(self):
        self._get_draft_tool().restore_players()

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
