#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor - Stage 3 (Players-first)
Windows-only. Live memory editing while NBA2K25 runs offline (EAC disabled).

Adds:
- Player Vitals editor (Face/Portrait/Headshot IDs, Position, Secondary Position,
  Play Initiator, Play Types 1-4).
- Name editing (First/Last) with UTF-16 writes.
- Body basics: Height and Wingspan via Appearance Data base, Weight at +0xF8.
- Badges editor with level mapping (None/Bronze/Silver/Gold/HOF).
- Existing Stage 2 categories remain: Attributes, Tendencies, Durability.
- CSV import/export per category.
- ctypes.wintypes.ULONG_PTR compatibility.

Place one offsets file (Offsets.txt / offsets.json / unified_offsets*.json|text / potion.txt)
in the same folder.
"""

import ctypes
from ctypes import wintypes
import sys, os, struct, csv, json, re
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_VERSION = "0.3"
MODULE_NAME = "NBA2K25.exe"

# -----------------------------
# Win32 interop
# -----------------------------

PROCESS_VM_READ      = 0x0010
PROCESS_VM_WRITE     = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION         = 0x0400
PROCESS_ALL_ACCESS = (
    PROCESS_VM_READ
    | PROCESS_VM_WRITE
    | PROCESS_VM_OPERATION
    | PROCESS_QUERY_INFORMATION
    | PROCESS_QUERY_LIMITED_INFORMATION
)

TH32CS_SNAPPROCESS  = 0x00000002
TH32CS_SNAPMODULE   = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Fallback for missing wintypes.ULONG_PTR
try:
    _ULONG_PTR = wintypes.ULONG_PTR  # type: ignore[attr-defined]
except Exception:
    _ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", wintypes.LPVOID),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", _ULONG_PTR),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]

CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
CreateToolhelp32Snapshot.restype  = wintypes.HANDLE

Module32FirstW = kernel32.Module32FirstW
Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
Module32FirstW.restype  = wintypes.BOOL

Module32NextW = kernel32.Module32NextW
Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
Module32NextW.restype  = wintypes.BOOL

Process32FirstW = kernel32.Process32FirstW
Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32FirstW.restype  = wintypes.BOOL

Process32NextW = kernel32.Process32NextW
Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32NextW.restype  = wintypes.BOOL

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype  = wintypes.HANDLE

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype  = wintypes.BOOL

ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
ReadProcessMemory.restype = wintypes.BOOL

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
WriteProcessMemory.restype = wintypes.BOOL

# -----------------------------
# Tables
# -----------------------------

PLAYER_STRIDE = 0x448
PLAYER_PTR_CHAINS: List[Tuple[int,int,bool]] = [
    (0x07E39430, 0x18, True),   # patch-known pointer path
    (0x06E14E48, 0x148, False),
    (0x07E5DD88, 0x0, False),
]

TEAM_NAME_OFFSET = 0x2D4
TEAM_NAME_LENGTH = 24

# For vitals enumerations
POSITION_MAP = {
    0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"
}
POSITION_REV = {v:k for k,v in POSITION_MAP.items()}

BADGE_LEVELS = ["None", "Bronze", "Silver", "Gold", "Hall of Fame"]
BADGE_REV = {name.lower(): idx for idx, name in enumerate(BADGE_LEVELS)}

# -----------------------------
# Memory helpers
# -----------------------------

@dataclass
class Player:
    index: int
    addr: int
    first: str
    last: str
    face_id: int
    team_name: str

class GameMemory:
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.pid: Optional[int] = None
        self.hproc: Optional[int] = None
        self.base: Optional[int] = None

    def _get_pid(self) -> Optional[int]:
        snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap:
            return None
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        try:
            ok = Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                if pe.szExeFile.lower() == self.module_name.lower():
                    return pe.th32ProcessID
                ok = Process32NextW(snap, ctypes.byref(pe))
        finally:
            CloseHandle(snap)
        return None

    def _get_module_base(self, pid: int, module_name: str) -> Optional[int]:
        flags = TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
        snap = CreateToolhelp32Snapshot(flags, pid)
        if not snap:
            return None
        me32 = MODULEENTRY32W()
        me32.dwSize = ctypes.sizeof(MODULEENTRY32W)
        try:
            if not Module32FirstW(snap, ctypes.byref(me32)):
                return None
            while True:
                if me32.szModule.lower() == module_name.lower():
                    return ctypes.cast(me32.modBaseAddr, ctypes.c_void_p).value
                if not Module32NextW(snap, ctypes.byref(me32)):
                    break
        finally:
            CloseHandle(snap)
        return None

    def open(self) -> bool:
        pid = self._get_pid()
        if pid is None:
            self.close()
            return False
        if self.pid == pid and self.hproc:
            return True
        handle = OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if not handle:
            self.close()
            return False
        base = self._get_module_base(pid, self.module_name)
        if base is None:
            CloseHandle(handle)
            self.close()
            return False
        self.pid = pid
        self.hproc = handle
        self.base = base
        return True

    def close(self):
        if self.hproc:
            try:
                CloseHandle(self.hproc)
            except Exception:
                pass
        self.pid = None
        self.hproc = None
        self.base = None

    def read(self, addr: int, size: int) -> bytes:
        if not self.hproc:
            return b""
        buf = (ctypes.c_ubyte * size)()
        n = ctypes.c_size_t(0)
        ok = ReadProcessMemory(self.hproc, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
        return bytes(buf[: n.value]) if ok else b""

    def write(self, addr: int, data: bytes) -> bool:
        if not self.hproc:
            return False
        n = ctypes.c_size_t(0)
        ok = WriteProcessMemory(self.hproc, ctypes.c_void_p(addr), data, len(data), ctypes.byref(n))
        return bool(ok and n.value == len(data))

def read_ptr(gm: GameMemory, addr: int) -> int:
    sz = ctypes.sizeof(ctypes.c_void_p)
    b = gm.read(addr, sz)
    if len(b) < sz:
        return 0
    return struct.unpack("<Q" if sz==8 else "<I", b)[0]

def read_u32(gm: GameMemory, addr: int) -> int:
    b = gm.read(addr, 4)
    return struct.unpack("<I", b)[0] if len(b) == 4 else 0

def write_u32(gm: GameMemory, addr: int, val: int) -> bool:
    return gm.write(addr, struct.pack("<I", int(val) & 0xFFFFFFFF))

def read_utf16(gm: GameMemory, addr: int, max_chars: int) -> str:
    raw = gm.read(addr, max_chars * 2)
    out = []
    for i in range(0, len(raw), 2):
        pair = raw[i:i+2]
        if len(pair) < 2:
            break
        if pair == b"\x00\x00":
            break
        out.append(pair)
    try:
        return b"".join(out).decode("utf-16le", errors="ignore")
    except Exception:
        return ""

def write_utf16(gm: GameMemory, addr: int, max_chars: int, text: str) -> bool:
    text = (text or "")[:max_chars]
    data = text.encode("utf-16le") + b"\x00\x00"
    total = max_chars * 2
    if len(data) < total:
        data += b"\x00" * (total - len(data))
    else:
        data = data[:total]
    return gm.write(addr, data)

# -----------------------------
# Pointer chain resolution
# -----------------------------

def resolve_table_base(gm: GameMemory, chains: List[Tuple[int,int,bool]]) -> Optional[int]:
    if not gm.base:
        return None
    for rva, final_off, extra_deref in chains:
        try:
            p = gm.base + rva
            q = read_ptr(gm, p)
            if not q:
                continue
            if extra_deref:
                q = read_ptr(gm, q)
                if not q:
                    continue
            base = q + final_off
            _ = gm.read(base, 8)
            return base
        except Exception:
            continue
    return None

# -----------------------------
# Offsets loader
# -----------------------------

UNIFIED_FILES = (
    "unified_offsets_mod.json",
    "unified_offsets_full.json",
    "unified_offsets_full.text",
    "unified_offsets.json",
    "offsets.json",
    "Offsets.txt",
    "potion.txt",
)

def _as_int(x: Any) -> Optional[int]:
    try:
        if isinstance(x, str):
            t = x.strip()
            if t.lower().startswith("0x"):
                return int(t, 16)
            if re.fullmatch(r"[0-9a-fA-F]+", t):
                # allow hex without 0x
                return int(t, 16)
            return int(t)
        if isinstance(x, (int, float)):
            return int(x)
    except Exception:
        return None
    return None

def load_offsets() -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    text = None
    for fname in UNIFIED_FILES:
        p = os.path.join(base_dir, fname)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
                break
            except Exception:
                continue
    base_info: Dict[str, Any] = {}
    categories: Dict[str, List[Dict[str, Any]]] = {}
    if not text:
        return base_info, categories

    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # Base
            if "Base" in data and isinstance(data["Base"], dict):
                for k, v in data["Base"].items():
                    iv = _as_int(v) if v is not None else None
                    base_info[k] = iv if iv is not None else v
            # Categories
            for k, arr in data.items():
                if k == "Base": continue
                if isinstance(arr, list):
                    norm = []
                    for item in arr:
                        if not isinstance(item, dict): continue
                        name = item.get("name") or item.get("Name")
                        off  = _as_int(item.get("offset") or item.get("Offset"))
                        sb   = _as_int(item.get("startBit") or item.get("StartBit") or 0) or 0
                        ln   = _as_int(item.get("length") or item.get("Length"))
                        if name and off is not None and ln is not None:
                            norm.append({"name": str(name), "offset": off, "startBit": sb, "length": ln})
                    if norm:
                        categories[k] = norm
            if categories:
                return base_info, categories
    except Exception:
        pass

    # Fallback: best-effort extraction for major categories
    for cat in ("Body","Vitals","Attributes","Tendencies","Durability","Badges"):
        m = re.search(rf'"{re.escape(cat)}"\s*:\s*\[', text)
        if not m: continue
        start = m.end()-1
        level, end = 0, None
        for i in range(start, len(text)):
            if text[i] == '[': level += 1
            elif text[i] == ']':
                level -= 1
                if level == 0: end = i; break
        if end is None: continue
        arr_text = text[start:end+1]
        try:
            arr = json.loads(arr_text)
            norm = []
            for item in arr:
                if not isinstance(item, dict): continue
                name = item.get("name") or item.get("Name")
                off  = _as_int(item.get("offset") or item.get("Offset"))
                sb   = _as_int(item.get("startBit") or item.get("StartBit") or 0) or 0
                ln   = _as_int(item.get("length") or item.get("Length"))
                if name and off is not None and ln is not None:
                    norm.append({"name": str(name), "offset": off, "startBit": sb, "length": ln})
            if norm:
                categories[cat] = norm
        except Exception:
            continue
    return base_info, categories

# -----------------------------
# Field IO helpers
# -----------------------------

def read_bits(gm: GameMemory, addr: int, byte_off: int, bit_off: int, bit_len: int) -> int:
    total_bits = bit_off + bit_len
    nbytes = (total_bits + 7) // 8
    data = gm.read(addr + byte_off, nbytes)
    if len(data) < nbytes:
        return 0
    val = int.from_bytes(data, "little")
    mask = (1 << bit_len) - 1
    return (val >> bit_off) & mask

def write_bits(gm: GameMemory, addr: int, byte_off: int, bit_off: int, bit_len: int, new_val: int) -> bool:
    total_bits = bit_off + bit_len
    nbytes = (total_bits + 7) // 8
    p = addr + byte_off
    data = gm.read(p, nbytes)
    if len(data) < nbytes:
        data = bytes([0]*nbytes)
    val = int.from_bytes(data, "little")
    mask = ((1 << bit_len) - 1) << bit_off
    val = (val & ~mask) | ((int(new_val) & ((1 << bit_len) - 1)) << bit_off)
    out = val.to_bytes(nbytes, "little")
    return gm.write(p, out)

# Rating conversions
RATING_MIN = 25
RATING_MAX_DISPLAY = 99
RATING_MAX_TRUE = 110

def raw_to_attr(raw: int, length: int) -> int:
    max_raw = (1<<length)-1
    if max_raw <= 0: return RATING_MIN
    # clamp into 25..99 using linear map
    r_true = RATING_MIN + (raw/max_raw) * (RATING_MAX_TRUE - RATING_MIN)
    r_disp = min(r_true, RATING_MAX_DISPLAY)
    return int(round(max(RATING_MIN, r_disp)))

def attr_to_raw(rating: float, length: int) -> int:
    max_raw = (1<<length)-1
    r = max(RATING_MIN, min(RATING_MAX_DISPLAY, float(rating)))
    frac = (r - RATING_MIN) / (RATING_MAX_TRUE - RATING_MIN)
    return max(0, min(max_raw, int(round(frac * max_raw))))

def raw_to_tendency(raw: int, length: int) -> int:
    max_raw = (1<<length)-1
    if max_raw <= 0: return 0
    return int(round((raw/max_raw)*100.0))

def tendency_to_raw(val: float, length: int) -> int:
    max_raw = (1<<length)-1
    v = max(0.0, min(100.0, float(val)))
    return max(0, min(max_raw, int(round((v/100.0)*max_raw))))

# -----------------------------
# Enumerators
# -----------------------------

def list_players(gm: GameMemory, base_first: int, base_last: int, base_face: int,
                 team_ptr_off: int, team_name_off: int, stride: int, max_players: int=6000) -> List[Player]:
    base = resolve_table_base(gm, PLAYER_PTR_CHAINS)
    if not base:
        return []
    out: List[Player] = []
    blanks = 0
    for i in range(max_players):
        addr = base + i*stride
        last = read_utf16(gm, addr + base_last, 20)
        first = read_utf16(gm, addr + base_first, 20)
        if not first and not last:
            blanks += 1
            if blanks > 200: break
            continue
        blanks = 0
        face = read_u32(gm, addr + base_face)
        team_ptr = read_ptr(gm, addr + team_ptr_off)
        team_name = ""
        if team_ptr:
            try:
                team_name = read_utf16(gm, team_ptr + team_name_off, 24)
            except Exception:
                team_name = ""
        out.append(Player(i, addr, first, last, face, team_name))
    return out

# -----------------------------
# GUI Grids
# -----------------------------

class CategoryGrid(ttk.Frame):
    def __init__(self, parent, gm: GameMemory, category_name: str,
                 fields: List[Dict[str, Any]],
                 base_info: Dict[str, Any]):
        super().__init__(parent)
        self.gm = gm
        self.cat = category_name
        self.fields = fields
        self.base_info = base_info
        self.player_addr: Optional[int] = None
        self.player_index: Optional[int] = None

        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Save All", command=self.save_all).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side=tk.LEFT, padx=12)
        ttk.Button(bar, text="Import CSV", command=self.import_csv).pack(side=tk.LEFT, padx=3)

        # Tree
        cols = ("name","value","raw","offset","startBit","length")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        headers = ["Field","Value","Raw","Off","SB","Len"]
        for c,h in zip(cols, headers):
            self.tree.heading(c, text=h)
            w = 230 if c=="name" else 90
            if c in ("offset","startBit","length"): w = 70
            self.tree.column(c, width=w, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Editor
        ed = ttk.Frame(self)
        ed.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(ed, text="Selected:").pack(side=tk.LEFT)
        self.sel_name = tk.StringVar()
        self.sel_val  = tk.StringVar()
        self.sel_raw  = tk.StringVar()
        ttk.Entry(ed, textvariable=self.sel_name, width=28, state="readonly").pack(side=tk.LEFT, padx=4)
        ttk.Label(ed, text="Value").pack(side=tk.LEFT, padx=(12,2))
        self.ent_val = ttk.Entry(ed, textvariable=self.sel_val, width=16)
        self.ent_val.pack(side=tk.LEFT)
        ttk.Label(ed, text="Raw").pack(side=tk.LEFT, padx=(12,2))
        self.ent_raw = ttk.Entry(ed, textvariable=self.sel_raw, width=10)
        self.ent_raw.pack(side=tk.LEFT)
        ttk.Button(ed, text="Write Field", command=self.write_selected).pack(side=tk.LEFT, padx=8)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    # Category-specific conversions
    def _to_display(self, raw: int, ln: int, name: str) -> str:
        if self.cat == "Tendencies":
            return str(raw_to_tendency(raw, ln))
        if self.cat == "Attributes" or self.cat == "Durability":
            return str(raw_to_attr(raw, ln))
        if self.cat == "Badges":
            lvl = raw if raw < len(BADGE_LEVELS) else 0
            return BADGE_LEVELS[lvl]
        if self.cat == "Vitals":
            nm = name.lower()
            if "position" in nm:
                return POSITION_MAP.get(raw, f"{raw}")
            if "play initiator" in nm:
                return "Yes" if raw else "No"
        # default
        return str(raw)

    def _from_display(self, disp: str, raw_text: str, ln: int, name: str) -> int:
        # raw wins if provided
        if raw_text.strip():
            try:
                return max(0, min((1<<ln)-1, int(raw_text.strip())))
            except Exception:
                pass
        if self.cat == "Tendencies":
            return tendency_to_raw(float(disp or 0), ln)
        if self.cat == "Attributes" or self.cat == "Durability":
            return attr_to_raw(float(disp or 0), ln)
        if self.cat == "Badges":
            key = disp.strip().lower()
            return BADGE_REV.get(key, 0)
        if self.cat == "Vitals":
            nm = name.lower()
            if "position" in nm:
                return POSITION_REV.get(disp.strip().upper(), 0)
            if "play initiator" in nm:
                return 1 if disp.strip().lower() in ("1","yes","true","on") else 0
        # default numeric
        try:
            return int(float(disp or 0))
        except Exception:
            return 0

    def _addr_for_field(self, base_addr: int, off: int, name: str) -> int:
        # Special case: Body category uses Appearance Data base for height/wingspan
        if self.cat == "Body":
            # Height/Wingspan are at appearance base + 0x0/0x2. Weight is at player + 0xF8.
            app_off = int(self.base_info.get("Offset Appearance Data") or 0)
            if name.lower().startswith("height") or name.lower().startswith("wingspan"):
                return base_addr + app_off + off
            # weight or other floats are direct
            return base_addr + off
        # default: direct
        return base_addr + off

    def set_player(self, idx: int, addr: int):
        self.player_index = idx
        self.player_addr = addr
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        if not self.player_addr:
            return
        for f in self.fields:
            name = str(f["name"])
            off  = int(f["offset"])
            sb   = int(f.get("startBit", 0))
            ln   = int(f["length"])
            addr = self._addr_for_field(self.player_addr, off, name)
            raw  = read_bits(self.gm, addr, 0, sb, ln) if sb or ln <= 16 else read_bits(self.gm, addr, 0, 0, ln)
            # Note: treat all as bitfields to be safe; offsets describe a field bit-length.
            # Compute display
            disp = self._to_display(raw, ln, name)
            self.tree.insert("", tk.END, values=(name, disp, raw, hex(off), sb, ln))

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel: return
        vals = self.tree.item(sel[0], "values")
        self.sel_name.set(vals[0]); self.sel_val.set(str(vals[1])); self.sel_raw.set(str(vals[2]))

    def write_selected(self):
        if not self.player_addr: 
            messagebox.showwarning("No player","Select a player first."); return
        sel = self.tree.selection()
        if not sel: return
        name, disp, raw, off_hex, sb, ln = self.tree.item(sel[0], "values")
        off = int(off_hex, 16); sb = int(sb); ln = int(ln)
        new_raw = self._from_display(self.sel_val.get(), self.sel_raw.get(), ln, name)
        addr = self._addr_for_field(self.player_addr, off, name)
        ok = write_bits(self.gm, addr, 0, sb, ln, new_raw)
        if not ok:
            messagebox.showerror("Write failed","Run as Administrator. Ensure EAC is disabled.")
            return
        # Refresh row
        raw = read_bits(self.gm, addr, 0, sb, ln)
        disp = self._to_display(raw, ln, name)
        self.tree.item(sel[0], values=(name, disp, raw, off_hex, sb, ln))

    def save_all(self):
        if not self.player_addr: return
        for iid in self.tree.get_children():
            name, disp, raw, off_hex, sb, ln = self.tree.item(iid, "values")
            off = int(off_hex, 16); sb = int(sb); ln = int(ln)
            new_raw = self._from_display(str(disp), str(raw), ln, name)
            addr = self._addr_for_field(self.player_addr, off, name)
            write_bits(self.gm, addr, 0, sb, ln, new_raw)
        self.refresh()

    def export_csv(self):
        if not self.player_addr: return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
                                            initialfile=f"{self.cat}_player_{self.player_index}.csv")
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["PlayerIndex","Field","Value","Raw","Offset","StartBit","Length"])
            for iid in self.tree.get_children():
                w.writerow(self.tree.item(iid, "values"))

    def import_csv(self):
        if not self.player_addr: return
        path = filedialog.askopenfilename(filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path: return
        try:
            with open(path,"r",encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    if "PlayerIndex" in row and row["PlayerIndex"]:
                        try:
                            if int(row["PlayerIndex"]) != int(self.player_index): 
                                continue
                        except Exception:
                            pass
                    name = row.get("Field") or row.get("Name") or ""
                    for iid in self.tree.get_children():
                        vals = self.tree.item(iid, "values")
                        if str(vals[0]).strip().lower() == name.strip().lower():
                            off_hex, sb, ln = vals[3], int(vals[4]), int(vals[5])
                            off = int(off_hex, 16)
                            new_raw = self._from_display(row.get("Value",""), row.get("Raw",""), ln, name)
                            addr = self._addr_for_field(self.player_addr, off, name)
                            write_bits(self.gm, addr, 0, sb, ln, new_raw)
                            break
            self.refresh()
            messagebox.showinfo("Imported", f"Applied CSV to {self.cat}.")
        except Exception as e:
            messagebox.showerror("Import error", str(e))

# -----------------------------
# App
# -----------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"NBA 2K25 Roster Editor - Stage 3 v{APP_VERSION}")
        self.geometry("1250x840")
        self.minsize(1040, 680)

        if sys.platform != "win32":
            messagebox.showerror("Unsupported","Windows-only tool.")
            self.destroy(); return

        self.gm = GameMemory(MODULE_NAME)
        self.connected = self.gm.open()

        self.base_info, self.categories = load_offsets()

        # Important base offsets from file (fallbacks if missing)
        self.off_first = int(self.base_info.get("Offset First Name") or 0x28)
        self.off_last  = int(self.base_info.get("Offset Last Name")  or 0x00)
        self.off_face  = int(self.base_info.get("Offset Face ID")    or 0x100)
        self.off_team_ptr = int(self.base_info.get("Offset Player Team") or 0x60)
        self.off_team_name = int(self.base_info.get("Offset Player Team Name") or 0x2D4)
        self.off_app  = int(self.base_info.get("Offset Appearance Data") or 0x78)

        # Concise map of fields per category
        self.fields_by_cat: Dict[str, List[Dict[str,Any]]] = {}
        for cat in ("Vitals","Attributes","Tendencies","Durability","Badges","Body"):
            if cat in self.categories:
                self.fields_by_cat[cat] = self._normalize(self.categories[cat])

        # State
        self.players: List[Player] = []
        self.base_table = resolve_table_base(self.gm, PLAYER_PTR_CHAINS) if self.connected else None
        self.selected_player_index: Optional[int] = None
        self.selected_player_addr: Optional[int] = None

        # UI
        self._build_ui()
        self._refresh_players()

    def _normalize(self, arr: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
        out = []
        for f in arr:
            name = str(f.get("name","")).strip()
            off  = _as_int(f.get("offset"))
            sb   = _as_int(f.get("startBit") or 0) or 0
            ln   = _as_int(f.get("length"))
            if name and off is not None and ln is not None:
                out.append({"name":name, "offset":int(off), "startBit":int(sb), "length":int(ln)})
        return out

    def _build_ui(self):
        main = ttk.Notebook(self); main.pack(fill=tk.BOTH, expand=True)

        # Home
        tab_home = ttk.Frame(main); main.add(tab_home, text="Home")
        ttk.Label(tab_home, text=self._status(), font=("Segoe UI", 12)).pack(anchor="w", padx=12, pady=12)
        ttk.Button(tab_home, text="Reconnect", command=self._reconnect).pack(anchor="w", padx=12, pady=4)
        ttk.Label(tab_home, text="Run as Administrator. EAC must be disabled.").pack(anchor="w", padx=12, pady=6)

        # Players
        tab_players = ttk.Frame(main); main.add(tab_players, text="Players")
        self._build_players(tab_players)

        # Data tabs
        tab_data = ttk.Notebook(main); main.add(tab_data, text="Player Data")

        # Vitals gets first tab
        self.grids: Dict[str, CategoryGrid] = {}
        for cat in ("Vitals","Attributes","Tendencies","Durability","Badges","Body"):
            if cat in self.fields_by_cat:
                frame = CategoryGrid(tab_data, self.gm, cat, self.fields_by_cat[cat], self.base_info)
                tab_data.add(frame, text=cat)
                self.grids[cat] = frame

    def _status(self) -> str:
        cats = ", ".join(self.fields_by_cat.keys()) if self.fields_by_cat else "none"
        return f"Process: {'connected' if self.connected else 'not found'} | Categories loaded: {cats}"

    def _reconnect(self):
        self.gm.close(); self.connected = self.gm.open()
        self.base_table = resolve_table_base(self.gm, PLAYER_PTR_CHAINS) if self.connected else None
        self._refresh_players()
        if self.selected_player_addr is not None and self.selected_player_index is not None:
            for grid in self.grids.values():
                grid.set_player(self.selected_player_index, self.selected_player_addr)

    # Players tab
    def _build_players(self, parent):
        top = ttk.Frame(parent); top.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(top, text="Search:").pack(side=tk.LEFT)
        self.var_search = tk.StringVar(); tk.Entry(top, textvariable=self.var_search, width=30).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="Find", command=self._filter_players).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Refresh", command=self._refresh_players).pack(side=tk.LEFT, padx=4)

        # Quick vitals editor
        quick = ttk.LabelFrame(parent, text="Quick Vitals")
        quick.pack(fill=tk.X, padx=8, pady=4)
        self.q_first = tk.StringVar(); self.q_last = tk.StringVar()
        self.q_face  = tk.StringVar()
        for lbl, var, width in (("First", self.q_first, 20),("Last", self.q_last, 20),("FaceID", self.q_face, 8)):
            ttk.Label(quick, text=lbl).pack(side=tk.LEFT, padx=(8,2)); ttk.Entry(quick, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0,10))
        ttk.Button(quick, text="Save Names/Face", command=self._save_quick_vitals).pack(side=tk.LEFT, padx=8)

        cols = ("index","first","last","face","team")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=18)
        for c in cols:
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=140 if c!="index" else 70, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_player)

    def _refresh_players(self):
        if not self.connected:
            self.players = []
        else:
            self.players = list_players(self.gm, self.off_first, self.off_last, self.off_face,
                                        self.off_team_ptr, self.off_team_name, PLAYER_STRIDE)
        self.tree.delete(*self.tree.get_children())
        for p in self.players:
            self.tree.insert("", tk.END, values=(p.index, p.first, p.last, p.face_id, p.team_name))

    def _filter_players(self):
        q = (self.var_search.get() or "").lower()
        self.tree.delete(*self.tree.get_children())
        for p in self.players:
            key = f"{p.first} {p.last} {p.team_name} {p.face_id}".lower()
            if q in key:
                self.tree.insert("", tk.END, values=(p.index, p.first, p.last, p.face_id, p.team_name))

    def _on_select_player(self, _e=None):
        sel = self.tree.selection()
        if not sel: return
        vals = self.tree.item(sel[0], "values")
        idx = int(vals[0])
        # compute address
        base = resolve_table_base(self.gm, PLAYER_PTR_CHAINS)
        if not base: return
        addr = base + idx*PLAYER_STRIDE
        self.selected_player_index = idx
        self.selected_player_addr = addr
        # fill quick box
        self.q_first.set(str(vals[1])); self.q_last.set(str(vals[2])); self.q_face.set(str(vals[3]))
        # push to grids
        for grid in self.grids.values():
            grid.set_player(idx, addr)

    def _save_quick_vitals(self):
        if self.selected_player_addr is None: return
        # First/Last as UTF16
        ok1 = write_utf16(self.gm, self.selected_player_addr + self.off_first, 20, self.q_first.get())
        ok2 = write_utf16(self.gm, self.selected_player_addr + self.off_last,  20, self.q_last.get())
        # Face ID
        try:
            fid = int(self.q_face.get())
        except Exception:
            fid = 0
        ok3 = write_u32(self.gm, self.selected_player_addr + self.off_face, fid)
        if ok1 and ok2 and ok3:
            self._refresh_players()
            messagebox.showinfo("Saved","Names/Face updated.")
        else:
            messagebox.showerror("Write failed","Run as Administrator. Ensure EAC is disabled.")

# -----------------------------
# main
# -----------------------------

def main():
    if sys.platform != "win32":
        print("Windows-only."); return
    app = App(); app.mainloop()

if __name__ == "__main__":
    main()
