
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor — Stage 5 (P0 Enhancer)
Windows-only. Requires an existing editor script (Stage 3.x or Stage 4) and offsets.json.
Adds:
- Global Undo/Redo by patching write_bits.
- Batch Ops: copy Attributes/Tendencies/Badges/Hotzones from one player to many; CSV export/import.

How to use:
  1) Put this file next to your editor (e.g., 2k25_roster_editor_stage4_players_gear_hotzones.py) and offsets.json
  2) Run: python 2k25_stage5_p0_launcher.py
  3) The base editor UI opens. A separate "Stage 5" window appears with Undo/Redo and Batch Ops.
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
    # fallback: open a file dialog
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

# Validate required symbols
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
        self.undo_stack = []  # list of ops: (gm, addr, byte_off, bit_off, bit_len, old_val, new_val)
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
# Batch Ops window
# -------------------------

class BatchOps(tk.Toplevel):
    def __init__(self, master, app, base_mod):
        super().__init__(master)
        self.title("Stage 5 — Batch Ops")
        self.geometry("900x600")
        self.app = app
        self.base = base_mod
        self.gm = app.gm
        self.base_info, self.categories = self.base.load_offsets()

        # restrict categories to these
        self.allowed_cats = [c for c in ("Attributes","Tendencies","Badges","Hotzones") if c in self.categories]

        # UI layout
        top = ttk.Frame(self); top.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(top, text="Category:").pack(side=tk.LEFT)
        self.var_cat = tk.StringVar(value=self.allowed_cats[0] if self.allowed_cats else "")
        self.cmb_cat = ttk.Combobox(top, values=self.allowed_cats, textvariable=self.var_cat, state="readonly", width=16)
        self.cmb_cat.pack(side=tk.LEFT, padx=6)
        self.cmb_cat.bind("<<ComboboxSelected>>", lambda _e: self._reload_fields())

        ttk.Button(top, text="Export CSV (selected targets)", command=self.export_csv).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Import CSV → apply", command=self.import_csv).pack(side=tk.LEFT, padx=4)

        body = ttk.Frame(self); body.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        # left: players lists
        left = ttk.Frame(body); left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="Source Player").pack(anchor="w")
        self.lst_src = tk.Listbox(left, height=10, exportselection=False)
        self.lst_src.pack(fill=tk.X, pady=(0,8))
        ttk.Label(left, text="Target Players (multi-select)").pack(anchor="w")
        self.lst_tgt = tk.Listbox(left, height=18, selectmode=tk.EXTENDED, exportselection=False)
        self.lst_tgt.pack(fill=tk.BOTH, expand=True)

        # right: fields
        right = ttk.Frame(body); right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0))
        ttk.Label(right, text="Fields in Category").pack(anchor="w")
        self.lst_fields = tk.Listbox(right, height=24, selectmode=tk.EXTENDED, exportselection=False)
        self.lst_fields.pack(fill=tk.BOTH, expand=True)
        self.chk_nonzero = tk.IntVar(value=0)
        ttk.Checkbutton(right, text="Only copy non-zero fields", variable=self.chk_nonzero).pack(anchor="w", pady=4)

        # bottom: actions + status
        bottom = ttk.Frame(self); bottom.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(bottom, text="Copy from Source → Targets", command=self.copy_apply).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Refresh player list", command=self._reload_players).pack(side=tk.LEFT, padx=8)
        self.var_status = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.var_status).pack(side=tk.RIGHT)

        self._reload_players()
        self._reload_fields()

    def _reload_players(self):
        # ensure base app player list is populated
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
        # Do the work
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
        # Build player index map
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
                    # pick player by index then name
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
# Stage 5 window
# -------------------------

class Stage5Window(tk.Toplevel):
    def __init__(self, master, app, base_mod, undo_mgr):
        super().__init__(master)
        self.title("Stage 5 — Tools")
        self.geometry("360x160")
        self.app = app
        self.base = base_mod
        self.undo = undo_mgr

        frm = ttk.Frame(self); frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        # Undo/Redo
        ttk.Label(frm, text="Undo/Redo").grid(row=0, column=0, sticky="w")
        ttk.Button(frm, text="Undo (Ctrl+Z)", command=self._undo).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(frm, text="Redo (Ctrl+Y)", command=self._redo).grid(row=1, column=1, sticky="w", pady=4, padx=(10,0))
        self.var_counts = tk.StringVar(value="0 undo / 0 redo")
        ttk.Label(frm, textvariable=self.var_counts).grid(row=2, column=0, columnspan=2, sticky="w")

        # Batch ops
        ttk.Label(frm, text="Batch").grid(row=3, column=0, sticky="w", pady=(10,0))
        ttk.Button(frm, text="Open Batch Ops", command=self._open_batch).grid(row=4, column=0, sticky="w", pady=4)

        # Key bindings
        try:
            app.bind_all("<Control-z>", lambda e: self._undo())
            app.bind_all("<Control-y>", lambda e: self._redo())
        except Exception:
            pass
        self._refresh_counts()

    def _refresh_counts(self):
        self.var_counts.set(f"{len(self.undo.undo_stack)} undo / {len(self.undo.redo_stack)} redo")

    def _undo(self):
        if self.undo.undo():
            self._refresh_counts()

    def _redo(self):
        if self.undo.redo():
            self._refresh_counts()

    def _open_batch(self):
        try:
            BatchOps(self.app, self.app, self.base)
        except Exception as e:
            messagebox.showerror("Batch Ops error", str(e))

# -------------------------
# Launch base app with tools
# -------------------------

def main():
    # Instantiate base app instead of calling its main()
    if sys.platform != "win32":
        print("Windows-only."); return
    app = base.App()
    # Pop our Stage 5 tools window
    app.after(500, lambda: Stage5Window(app, app, base, undo_manager))
    app.mainloop()

if __name__ == "__main__":
    main()
