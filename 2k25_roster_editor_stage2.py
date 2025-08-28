#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor - Stage 2 (Attributes/Tendencies/Durability)
Windows-only. Live memory editing when NBA2K25 runs offline (EAC disabled).

This build adds:
- Bitfield read/write for Attributes, Tendencies, Durability using offsets map.
- Dynamic category loader from offsets.json / potion.txt / Offsets.txt family.
- Grids per category with editable values and CSV import/export.
- Compatibility fix for ctypes.wintypes.ULONG_PTR on some Python builds.

Run:
    python 2k25_roster_editor_stage2.py
"""

import ctypes
from ctypes import wintypes
import sys
import os
import struct
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

# -----------------------------
# Constants
# -----------------------------
MODULE_NAME      = "NBA2K25.exe"

# Player table pointer chains and layout
PLAYER_TABLE_RVA = 0x07E52998      # static RVA fallback
PLAYER_STRIDE    = 0x448           # 1096 bytes per player

PLAYER_PTR_CHAINS: List[Tuple[int, int, bool]] = [
    (0x07E39430, 0x18, True),      # [[base+7E39430]] + 0x18  (Patch 4)
    (0x06E14E48, 0x148, False),    # [base+6E14E48] + 0x148   (Edit Player table)
    (PLAYER_TABLE_RVA, 0x0, False),
    (0x07E5DD88, 0x0, False),
]

# Player fields
OFF_LAST_NAME    = 0x000           # UTF-16, 20 chars
OFF_FIRST_NAME   = 0x028           # UTF-16, 20 chars
NAME_MAX_CHARS   = 20
OFF_FACE_ID      = 0x114           # u32
OFF_TEAM_PTR     = 0x060           # pointer to team struct

# Team layout for display
TEAM_STRIDE            = 0x654     # bytes per team record
TEAM_NAME_OFFSET       = 0x2D4     # UTF-16, 24 chars
TEAM_NAME_LENGTH       = 24

TEAM_PTR_CHAINS: List[Tuple[int, int, bool]] = [
    (0x07E39430, 0x88, True),
    (0x07E39430, 0x68, True),
    (0x07DFFAC0, 0x68, True),
    (0x07E39430, 0x0, False),
    (0x07DFFAC0, 0x0, False),
]

# Stadium table included only for pointer testing; Stage 2 focuses on player data.
STADIUM_PTR_CHAINS: List[Tuple[int, int, bool]] = [
    (0x07E39430, 0x68, True),
    (0x07DFFAC0, 0x68, True),
    (0x07E39430, 0x0, False),
    (0x07DFFAC0, 0x0, False),
]

MAX_PLAYERS = 6000
APP_VERSION = "0.2"

# Category names targeted in Stage 2
TARGET_CATEGORIES = ("Attributes", "Tendencies", "Durability")

# -----------------------------
# Windows process primitives
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

# Compatibility for Python builds missing wintypes.ULONG_PTR
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

    # Low-level IO
    def read(self, addr: int, size: int) -> bytes:
        if not self.hproc:
            raise RuntimeError("process not open")
        buf = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = ReadProcessMemory(self.hproc, ctypes.c_void_p(addr), buf, size, ctypes.byref(read))
        if not ok:
            return bytes()
        return bytes(buf[:read.value])

    def write(self, addr: int, data: bytes) -> bool:
        if not self.hproc:
            raise RuntimeError("process not open")
        written = ctypes.c_size_t(0)
        ok = WriteProcessMemory(self.hproc, ctypes.c_void_p(addr), data, len(data), ctypes.byref(written))
        return bool(ok and written.value == len(data))

# -----------------------------
# Encoding helpers
# -----------------------------

def read_utf16(gm: GameMemory, addr: int, max_chars: int) -> str:
    raw = gm.read(addr, max_chars * 2)
    out = []
    for i in range(0, len(raw), 2):
        ch = raw[i:i+2]
        if len(ch) < 2:
            break
        if ch == b"\x00\x00":
            break
        out.append(ch)
    try:
        return b"".join(out).decode("utf-16le", errors="ignore")
    except Exception:
        return ""

def write_utf16(gm: GameMemory, addr: int, max_chars: int, text: str) -> bool:
    text = (text or "")[:max_chars]
    data = text.encode("utf-16le")
    pad_len = max_chars * 2 - len(data)
    if pad_len < 0:
        data = data[:max_chars*2]
        pad_len = 0
    # ensure terminator and pad
    data += b"\x00\x00"
    if pad_len > 2:
        data += b"\x00" * (pad_len - 2)
    return gm.write(addr, data)

def read_u32(gm: GameMemory, addr: int) -> int:
    b = gm.read(addr, 4)
    if len(b) < 4:
        return 0
    return struct.unpack("<I", b)[0]

def write_u32(gm: GameMemory, addr: int, value: int) -> bool:
    return gm.write(addr, struct.pack("<I", int(value) & 0xFFFFFFFF))

def read_ptr(gm: GameMemory, addr: int) -> int:
    sz = ctypes.sizeof(ctypes.c_void_p)
    b = gm.read(addr, sz)
    if len(b) < sz:
        return 0
    return struct.unpack("<Q" if sz==8 else "<I", b)[0]

# -----------------------------
# Pointer chain resolution
# -----------------------------

def resolve_table_base(gm: GameMemory, chains: List[Tuple[int,int,bool]]) -> Optional[int]:
    if not gm.base:
        return None
    for rva, final_off, extra_deref in chains:
        try:
            p = gm.base + rva
            ptr = read_ptr(gm, p)
            if not ptr:
                continue
            if extra_deref:
                ptr = read_ptr(gm, ptr)
                if not ptr:
                    continue
            base = ptr + final_off
            # sanity
            _ = gm.read(base, 8)
            return base
        except Exception:
            continue
    return None

# -----------------------------
# Enumerators
# -----------------------------

def list_players(gm: GameMemory) -> List[Player]:
    base = resolve_table_base(gm, PLAYER_PTR_CHAINS)
    if not base:
        return []
    out: List[Player] = []
    blanks = 0
    for i in range(MAX_PLAYERS):
        addr = base + i * PLAYER_STRIDE
        last = read_utf16(gm, addr + OFF_LAST_NAME, NAME_MAX_CHARS)
        first = read_utf16(gm, addr + OFF_FIRST_NAME, NAME_MAX_CHARS)
        if not first and not last:
            blanks += 1
            if blanks > 200:
                break
            continue
        blanks = 0
        face = read_u32(gm, addr + OFF_FACE_ID)
        team_ptr = read_ptr(gm, addr + OFF_TEAM_PTR)
        team_name = ""
        if team_ptr:
            try:
                team_name = read_utf16(gm, team_ptr + TEAM_NAME_OFFSET, TEAM_NAME_LENGTH)
            except Exception:
                team_name = ""
        out.append(Player(i, addr, first, last, face, team_name))
    return out

# -----------------------------
# Offsets map loader
# -----------------------------

import json
import re
from pathlib import Path

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
            x = x.strip()
            if x.lower().startswith("0x"):
                return int(x, 16)
            # allow hex without 0x
            if re.fullmatch(r"[0-9a-fA-F]+", x):
                return int(x, 16)
            return int(x)
        if isinstance(x, (int, float)):
            return int(x)
    except Exception:
        return None
    return None

def load_offsets_map() -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns dict: category -> list of fields{ name, offset, startBit, length }
    Accepts several file names in script directory.
    """
    base_dir = Path(__file__).resolve().parent
    text = None
    src = None
    for fname in UNIFIED_FILES:
        p = base_dir / fname
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
                src = fname
                break
            except Exception:
                continue
    categories: Dict[str, List[Dict[str, Any]]] = {}

    if text is None:
        return categories

    # Try JSON first, with cleanup of trailing commas
    try:
        cleaned = re.sub(r",\s*([}\]])", r"\\1", text)
        data = json.loads(text)
        if isinstance(data, dict):
            for k, v in data.items():
                if k.lower() == "base":
                    continue
                if isinstance(v, list):
                    # ensure required fields are present
                    norm = []
                    for item in v:
                        if not isinstance(item, dict):
                            continue
                        name = item.get("name") or item.get("Name") or item.get("field") or item.get("Field")
                        off  = item.get("offset") or item.get("Offset")
                        sb   = item.get("startBit") or item.get("StartBit") or item.get("start") or item.get("Start")
                        ln   = item.get("length") or item.get("Length") or item.get("bits") or item.get("Bits")
                        oi = _as_int(off)
                        sbi = _as_int(sb) if sb is not None else 0
                        lni = _as_int(ln) if ln is not None else None
                        if name and oi is not None and lni is not None:
                            norm.append({"name": str(name), "offset": oi, "startBit": sbi, "length": lni})
                    if norm:
                        categories[k] = norm
        if categories:
            return categories
    except Exception:
        pass

    # Fallback: extract arrays for known categories using regex
    try:
        for cat in TARGET_CATEGORIES + ("Body", "Vitals", "Badges"):
            m = re.search(rf'"{re.escape(cat)}"\s*:\s*\[', text)
            if not m:
                continue
            start = m.end() - 1
            level = 0
            end = None
            for i in range(start, len(text)):
                if text[i] == '[':
                    level += 1
                elif text[i] == ']':
                    level -= 1
                    if level == 0:
                        end = i
                        break
            if end is None:
                continue
            arr_text = text[start:end+1]
            arr_text = re.sub(r",\s*([}\]])", r"\\1", arr_text)
            try:
                arr = json.loads(arr_text)
                norm = []
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name") or item.get("Name")
                    off  = _as_int(item.get("offset") or item.get("Offset"))
                    sb   = _as_int(item.get("startBit") or item.get("StartBit") or 0)
                    ln   = _as_int(item.get("length") or item.get("Length"))
                    if name and off is not None and ln is not None:
                        norm.append({"name": str(name), "offset": off, "startBit": sb or 0, "length": ln})
                if norm:
                    categories[cat] = norm
            except Exception:
                continue
    except Exception:
        pass
    return categories

# -----------------------------
# Bitfield IO
# -----------------------------

def read_bits(gm: GameMemory, base_addr: int, byte_off: int, bit_off: int, bit_len: int) -> int:
    # Number of bytes needed from the starting byte
    total_bits = bit_off + bit_len
    num_bytes = (total_bits + 7) // 8
    data = gm.read(base_addr + byte_off, num_bytes)
    if len(data) < num_bytes:
        return 0
    # Little-endian integer
    val = int.from_bytes(data, "little")
    mask = (1 << bit_len) - 1
    return (val >> bit_off) & mask

def write_bits(gm: GameMemory, base_addr: int, byte_off: int, bit_off: int, bit_len: int, new_val: int) -> bool:
    total_bits = bit_off + bit_len
    num_bytes = (total_bits + 7) // 8
    addr = base_addr + byte_off
    data = gm.read(addr, num_bytes)
    if len(data) < num_bytes:
        # if read fails, create zero buffer to attempt write
        data = bytes([0] * num_bytes)
    val = int.from_bytes(data, "little")
    mask = ((1 << bit_len) - 1) << bit_off
    val = (val & ~mask) | ((int(new_val) & ((1 << bit_len) - 1)) << bit_off)
    out = val.to_bytes(num_bytes, "little")
    return gm.write(addr, out)

# -----------------------------
# Rating conversions
# -----------------------------

RATING_MIN = 25
RATING_MAX_DISPLAY = 99
RATING_MAX_TRUE = 110

def convert_raw_to_rating(raw: int, length: int) -> int:
    max_raw = (1 << length) - 1
    if max_raw <= 0:
        return RATING_MIN
    # Most attributes map near raw-10 (observed). Clamp to 25-99.
    if max_raw >= (RATING_MAX_DISPLAY - 10):
        rating = raw + 10
        if rating < RATING_MIN:
            rating = RATING_MIN
        elif rating > RATING_MAX_DISPLAY:
            rating = RATING_MAX_DISPLAY
        return int(rating)
    # Linear map into 25-110 then clamp display to 99
    rating_true = RATING_MIN + (raw / max_raw) * (RATING_MAX_TRUE - RATING_MIN)
    if rating_true < RATING_MIN:
        rating_true = RATING_MIN
    elif rating_true > RATING_MAX_TRUE:
        rating_true = RATING_MAX_TRUE
    rating_display = min(rating_true, RATING_MAX_DISPLAY)
    return int(round(rating_display))

def convert_rating_to_raw(rating: float, length: int) -> int:
    max_raw = (1 << length) - 1
    if max_raw <= 0:
        return 0
    r = float(rating)
    if r < RATING_MIN:
        r = RATING_MIN
    elif r > RATING_MAX_DISPLAY:
        r = RATING_MAX_DISPLAY
    if max_raw >= (RATING_MAX_DISPLAY - 10):
        raw_val = round(r - 10)
        return max(0, min(max_raw, raw_val))
    fraction = (r - RATING_MIN) / (RATING_MAX_TRUE - RATING_MIN)
    fraction = min(1.0, max(0.0, fraction))
    raw_val = round(fraction * max_raw)
    return max(0, min(max_raw, raw_val))

def convert_tendency_raw_to_rating(raw: int, length: int) -> int:
    max_raw = (1 << length) - 1
    if max_raw <= 0:
        return 0
    rating = (raw / max_raw) * 100.0
    return int(round(min(100.0, max(0.0, rating))))

def convert_rating_to_tendency_raw(rating: float, length: int) -> int:
    max_raw = (1 << length) - 1
    if max_raw <= 0:
        return 0
    r = min(100.0, max(0.0, float(rating)))
    fraction = r / 100.0
    raw_val = round(fraction * max_raw)
    return max(0, min(max_raw, raw_val))

# -----------------------------
# GUI helpers
# -----------------------------

class CategoryGrid(ttk.Frame):
    """Grid editor for one category across all fields."""
    def __init__(self, parent, gm: GameMemory, category_name: str, fields: List[Dict[str, Any]]):
        super().__init__(parent)
        self.gm = gm
        self.cat = category_name
        self.fields = fields  # list of dict(name, offset, startBit, length)
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
        cols = ("name", "value", "raw", "offset", "startBit", "length")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        heads = ["Field", "Value", "Raw", "Off", "SB", "Len"]
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h)
            w = 230 if c=="name" else 90
            if c in ("offset","startBit","length"):
                w = 70
            self.tree.column(c, width=w, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Editor row below tree
        ed = ttk.Frame(self)
        ed.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(ed, text="Selected:").pack(side=tk.LEFT)
        self.sel_name = tk.StringVar(value="")
        self.sel_val  = tk.StringVar(value="")
        self.sel_raw  = tk.StringVar(value="")
        ttk.Entry(ed, textvariable=self.sel_name, width=28, state="readonly").pack(side=tk.LEFT, padx=4)
        ttk.Label(ed, text="Value").pack(side=tk.LEFT, padx=(12,2))
        self.ent_val = ttk.Entry(ed, textvariable=self.sel_val, width=10)
        self.ent_val.pack(side=tk.LEFT)
        ttk.Label(ed, text="Raw").pack(side=tk.LEFT, padx=(12,2))
        self.ent_raw = ttk.Entry(ed, textvariable=self.sel_raw, width=10)
        self.ent_raw.pack(side=tk.LEFT)
        ttk.Button(ed, text="Write Field", command=self.write_selected).pack(side=tk.LEFT, padx=8)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def set_player(self, idx: int, addr: int):
        self.player_index = idx
        self.player_addr = addr
        self.refresh()

    def _conv_pair(self, raw: int, length: int) -> Tuple[int,int]:
        if self.cat == "Tendencies":
            return convert_tendency_raw_to_rating(raw, length), raw
        # Durability + Attributes => 25-99 scale by default
        return convert_raw_to_rating(raw, length), raw

    def _rev_pair(self, val: str, raw: str, length: int) -> int:
        # Prefer raw if user provided it; else convert display value
        if raw.strip():
            try:
                v = int(raw.strip())
                return max(0, min((1<<length)-1, v))
            except Exception:
                pass
        try:
            vdisp = float(val.strip())
        except Exception:
            vdisp = 0.0
        if self.cat == "Tendencies":
            return convert_rating_to_tendency_raw(vdisp, length)
        return convert_rating_to_raw(vdisp, length)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        if not self.player_addr:
            return
        for f in self.fields:
            name = f["name"]
            off  = int(f["offset"])
            sb   = int(f.get("startBit", 0))
            ln   = int(f["length"])
            raw = read_bits(self.gm, self.player_addr, off, sb, ln)
            val, r = self._conv_pair(raw, ln)
            self.tree.insert("", tk.END, values=(name, val, r, hex(off), sb, ln))

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        self.sel_name.set(vals[0])
        self.sel_val.set(str(vals[1]))
        self.sel_raw.set(str(vals[2]))

    def write_selected(self):
        if not self.player_addr:
            messagebox.showwarning("No player", "Select a player first.")
            return
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        name, _val, _raw, off_hex, sb, ln = vals
        off = int(off_hex, 16)
        sb  = int(sb)
        ln  = int(ln)
        new_raw = self._rev_pair(self.sel_val.get(), self.sel_raw.get(), ln)
        ok = write_bits(self.gm, self.player_addr, off, sb, ln, new_raw)
        if ok:
            # refresh row
            raw = read_bits(self.gm, self.player_addr, off, sb, ln)
            val, r = self._conv_pair(raw, ln)
            self.tree.item(sel[0], values=(name, val, r, off_hex, sb, ln))
        else:
            messagebox.showerror("Write failed", "Run as Administrator. Ensure EAC is disabled.")

    def save_all(self):
        if not self.player_addr:
            messagebox.showwarning("No player", "Select a player first.")
            return
        # Iterate rows, compute raw from current 'Value' and 'Raw' cells
        for iid in self.tree.get_children():
            name, val, raw, off_hex, sb, ln = self.tree.item(iid, "values")
            off = int(off_hex, 16); sb = int(sb); ln = int(ln)
            new_raw = self._rev_pair(str(val), str(raw), ln)
            write_bits(self.gm, self.player_addr, off, sb, ln, new_raw)
        self.refresh()

    def export_csv(self):
        if not self.player_addr:
            messagebox.showwarning("No player", "Select a player first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")],
                                            initialfile=f"{self.cat}_player_{self.player_index}.csv")
        if not path:
            return
        # Export current player's fields
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["PlayerIndex", "Field", "Value", "Raw", "Offset", "StartBit", "Length"])
            for iid in self.tree.get_children():
                name, val, raw, off_hex, sb, ln = self.tree.item(iid, "values")
                w.writerow([self.player_index, name, val, raw, off_hex, sb, ln])
        messagebox.showinfo("Exported", f"Saved {self.cat} CSV.")

    def import_csv(self):
        if not self.player_addr:
            messagebox.showwarning("No player", "Select a player first.")
            return
        path = filedialog.askopenfilename(filetypes=[("CSV","*.csv"),("All","*.*")])
        if not path:
            return
        rows = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    rows.append(row)
        except Exception as e:
            messagebox.showerror("Read error", str(e))
            return
        # Apply only rows for this player (by PlayerIndex if present; else all rows)
        for row in rows:
            if "PlayerIndex" in row and row["PlayerIndex"]:
                try:
                    if int(row["PlayerIndex"]) != int(self.player_index):
                        continue
                except Exception:
                    pass
            name = row.get("Field") or row.get("Name")
            if not name:
                continue
            # find matching row by field name
            match_iid = None
            for iid in self.tree.get_children():
                vals = self.tree.item(iid, "values")
                if str(vals[0]).strip().lower() == str(name).strip().lower():
                    match_iid = iid
                    break
            if not match_iid:
                continue
            # Use provided Raw if present; else Value
            val = row.get("Value","")
            raw = row.get("Raw","")
            vals = self.tree.item(match_iid, "values")
            _, _, _, off_hex, sb, ln = vals
            off = int(off_hex, 16); sb = int(sb); ln = int(ln)
            new_raw = self._rev_pair(str(val), str(raw), ln)
            write_bits(self.gm, self.player_addr, off, sb, ln, new_raw)
        self.refresh()
        messagebox.showinfo("Imported", f"Applied CSV to {self.cat}.")

# -----------------------------
# App GUI
# -----------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"NBA 2K25 Roster Editor - Stage 2 v{APP_VERSION}")
        self.geometry("1200x780")
        self.minsize(1000, 640)

        if sys.platform != "win32":
            messagebox.showerror("Unsupported", "Windows-only tool.")
            self.destroy()
            return

        self.gm = GameMemory(MODULE_NAME)
        self.connected = self.gm.open()

        self.categories = load_offsets_map()
        self.fields_by_cat: Dict[str, List[Dict[str, Any]]] = {
            cat: self._normalize_fields(lst) for cat, lst in self.categories.items()
            if cat in TARGET_CATEGORIES
        }

        self.players: List[Player] = []
        self.selected_player_addr: Optional[int] = None
        self.selected_player_index: Optional[int] = None

        self._build_ui()
        self._refresh_players()

    def _normalize_fields(self, lst: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
        # Ensure keys and ints
        out = []
        for f in lst:
            name = str(f.get("name","")).strip()
            off  = _as_int(f.get("offset"))
            sb   = _as_int(f.get("startBit") or 0) or 0
            ln   = _as_int(f.get("length"))
            if name and off is not None and ln is not None:
                out.append({"name": name, "offset": int(off), "startBit": int(sb), "length": int(ln)})
        return out

    def _build_ui(self):
        main = ttk.Notebook(self)
        main.pack(fill=tk.BOTH, expand=True)

        # Home
        tab_home = ttk.Frame(main)
        main.add(tab_home, text="Home")
        ttk.Label(tab_home, text=self._status_text(), font=("Segoe UI", 12)).pack(anchor="w", padx=12, pady=12)
        ttk.Button(tab_home, text="Reconnect", command=self._reconnect).pack(anchor="w", padx=12, pady=4)
        ttk.Label(tab_home, text=(
            "Offline = EAC disabled. Live memory editing only.\n"
            "Run editor as Administrator."
        )).pack(anchor="w", padx=12, pady=6)

        # Players
        tab_players = ttk.Frame(main)
        main.add(tab_players, text="Players")
        self._build_players(tab_players)

        # Player Data tabs (Stage 2)
        self.tab_data = ttk.Notebook(main)
        main.add(self.tab_data, text="Player Data")
        self.grids: Dict[str, CategoryGrid] = {}
        for cat in TARGET_CATEGORIES:
            fields = self.fields_by_cat.get(cat, [])
            frame = CategoryGrid(self.tab_data, self.gm, cat, fields)
            self.tab_data.add(frame, text=cat)
            self.grids[cat] = frame

    def _status_text(self) -> str:
        cats = [c for c in TARGET_CATEGORIES if self.fields_by_cat.get(c)]
        return f"Process: {'connected' if self.connected else 'not found'}  |  Module: {MODULE_NAME}  |  Categories loaded: {', '.join(cats) if cats else 'none'}"

    def _reconnect(self):
        self.gm.close()
        self.connected = self.gm.open()
        self._refresh_players()
        # Refresh grids if a player is selected
        if self.selected_player_addr is not None and self.selected_player_index is not None:
            for grid in self.grids.values():
                grid.set_player(self.selected_player_index, self.selected_player_addr)
        # Update home status
        for child in self.children.values():
            pass  # simple

    # Players tab UI
    def _build_players(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=8, pady=6)

        ttk.Label(toolbar, text="Search:").pack(side=tk.LEFT)
        self.player_search = tk.StringVar()
        tk.Entry(toolbar, textvariable=self.player_search, width=30).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="Find", command=self._filter_players).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="Refresh", command=self._refresh_players).pack(side=tk.LEFT, padx=4)

        cols = ("index", "first", "last", "face", "team")
        self.player_tree = ttk.Treeview(parent, columns=cols, show="headings", height=20)
        for c in cols:
            self.player_tree.heading(c, text=c.capitalize())
            self.player_tree.column(c, width=140 if c!="index" else 70, anchor=tk.W)
        self.player_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.player_tree.bind("<<TreeviewSelect>>", self._on_player_select)

    def _refresh_players(self):
        self.players = list_players(self.gm) if self.connected else []
        self.player_tree.delete(*self.player_tree.get_children())
        for p in self.players:
            self.player_tree.insert("", tk.END, values=(p.index, p.first, p.last, p.face_id, p.team_name))

    def _filter_players(self):
        q = (self.player_search.get() or "").lower()
        self.player_tree.delete(*self.player_tree.get_children())
        for p in self.players:
            key = f"{p.first} {p.last} {p.team_name} {p.face_id}".lower()
            if q in key:
                self.player_tree.insert("", tk.END, values=(p.index, p.first, p.last, p.face_id, p.team_name))

    def _on_player_select(self, _evt=None):
        sel = self.player_tree.selection()
        if not sel:
            return
        vals = self.player_tree.item(sel[0], "values")
        idx = int(vals[0])
        p = next((x for x in self.players if x.index == idx), None)
        if not p:
            return
        base = resolve_table_base(self.gm, PLAYER_PTR_CHAINS)
        if not base:
            self.selected_player_addr = None
            self.selected_player_index = None
            return
        self.selected_player_index = p.index
        self.selected_player_addr = base + p.index * PLAYER_STRIDE
        # Push selection into grids
        for grid in self.grids.values():
            grid.set_player(self.selected_player_index, self.selected_player_addr)

# -----------------------------
# Main
# -----------------------------

def main():
    if sys.platform != "win32":
        print("Windows-only.")
        return
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
