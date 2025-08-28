#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NBA 2K25 Roster Editor Core (v0.1)
- Windows-only. Live memory editing only. "Offline" == game running with EAC disabled.
- No save-file parsing. No network.
- Features:
  * Resolve player/team/stadium tables via multiple pointer chains.
  * List players with Team, allow edit: First/Last name, Face ID.
  * Rename team fields: Team Name, City, City Short, State Short, Arena Name.
  * Edit stadium fields: Arena Name, Nickname, City, Short codes, Dornas 1-6.
  * Basic search + refresh.
  * Safe UTF-16LE writing with truncation to field lengths.

Run:
    python 2k25_roster_editor_core.py
"""

import ctypes
from ctypes import wintypes
import sys
import struct
import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass
from typing import Optional, List, Tuple

# -----------------------------
# Constants from user materials
# -----------------------------

MODULE_NAME      = "NBA2K25.exe"

# Player table pointer chains and layout (stride and fields)
# Sources: user "Edit Player" table and prototypes.
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

# Team layout (used when dereferencing player.team_ptr)
TEAM_STRIDE            = 0x654     # bytes per team record
TEAM_NAME_OFFSET       = 0x2D4     # UTF-16, 24 chars
TEAM_NAME_LENGTH       = 24
TEAM_PLAYER_SLOT_COUNT = 30

TEAM_PTR_CHAINS: List[Tuple[int, int, bool]] = [
    (0x07E39430, 0x88, True),      # [[base+7E39430]] + 0x88
    (0x07E39430, 0x68, True),      # [[base+7E39430]] + 0x68
    (0x07DFFAC0, 0x68, True),
    (0x07E39430, 0x0, False),
    (0x07DFFAC0, 0x0, False),
]

TEAM_FIELDS: dict[str, Tuple[int, int]] = {
    "Team Name": (0x2D4, 24),
    "City Name": (0x306, 24),
    "City Short Name": (0x32A, 8),
    "State Short Name": (0x338, 8),
    "Arena Name": (0x158, 36),
}

# Stadium layout
STADIUM_STRIDE = 0x12B0            # 4784 bytes
STADIUM_PTR_CHAINS: List[Tuple[int, int, bool]] = [
    (0x07E39430, 0x68, True),
    (0x07DFFAC0, 0x68, True),
    (0x07E39430, 0x0, False),
    (0x07DFFAC0, 0x0, False),
]
STADIUM_FIELDS: dict[str, Tuple[int, int, bool]] = {
    "Arena Name": (0x0, 36, True),
    "Nickname": (0x42, 36, True),
    "City Name": (0x82, 36, True),
    "City Short Name": (0x180, 8, True),
    "State Short Name": (0x192, 8, True),
    "Dornas File 1": (0x198, 48, True),
    "Dornas File 2": (0x298, 48, True),
    "Dornas File 3": (0x398, 48, True),
    "Dornas File 4": (0x498, 48, True),
    "Dornas File 5": (0x598, 48, True),
    "Dornas File 6": (0x698, 48, True),
}

MAX_PLAYERS = 6000
MAX_TEAMS_SCAN = 150
APP_VERSION = "0.1"

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
        ("th32DefaultHeapID", wintypes.ULONG_PTR),
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

@dataclass
class Team:
    index: int
    addr: int
    name: str
    city: str
    city_short: str
    state_short: str
    arena_name: str

@dataclass
class Stadium:
    index: int
    addr: int
    fields: dict

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
        if not ok or read.value != size:
            # partial read acceptable if string; return what we have
            return bytes(buf[:read.value])
        return bytes(buf)

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
    # Stop at first 0x0000
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
    # zero-pad to field length and ensure terminator
    pad_len = max_chars * 2 - len(data)
    if pad_len < 0:
        data = data[:max_chars*2]
        pad_len = 0
    data += b"\x00\x00" * 1  # explicit terminator (consumes 2 bytes but okay if truncated)
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
    b = gm.read(addr, ctypes.sizeof(ctypes.c_void_p))
    if len(b) < ctypes.sizeof(ctypes.c_void_p):
        return 0
    return struct.unpack("<Q" if ctypes.sizeof(ctypes.c_void_p)==8 else "<I", b)[0]

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
            if extra_deref:
                ptr = read_ptr(gm, ptr)
            base = ptr + final_off
            # sanity: base should be non-zero and readable
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
            if blanks > 200:  # stop if long blank stretch
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

def list_teams(gm: GameMemory) -> List[Team]:
    base = resolve_table_base(gm, TEAM_PTR_CHAINS)
    if not base:
        return []
    out: List[Team] = []
    for i in range(MAX_TEAMS_SCAN):
        addr = base + i * TEAM_STRIDE
        name = read_utf16(gm, addr + TEAM_FIELDS["Team Name"][0], TEAM_FIELDS["Team Name"][1])
        if not name:
            # allow a couple blanks then stop
            if i > 5:
                break
            else:
                continue
        city = read_utf16(gm, addr + TEAM_FIELDS["City Name"][0], TEAM_FIELDS["City Name"][1])
        city_s = read_utf16(gm, addr + TEAM_FIELDS["City Short Name"][0], TEAM_FIELDS["City Short Name"][1])
        state_s = read_utf16(gm, addr + TEAM_FIELDS["State Short Name"][0], TEAM_FIELDS["State Short Name"][1])
        arena = read_utf16(gm, addr + TEAM_FIELDS["Arena Name"][0], TEAM_FIELDS["Arena Name"][1])
        out.append(Team(i, addr, name, city, city_s, state_s, arena))
    return out

def list_stadiums(gm: GameMemory) -> List[Stadium]:
    base = resolve_table_base(gm, STADIUM_PTR_CHAINS)
    if not base:
        return []
    out: List[Stadium] = []
    # Scan a reasonable number; stadium count ~30
    for i in range(120):
        addr = base + i * STADIUM_STRIDE
        # Requires at least arena name non-empty to count
        an_off, an_len, an_unicode = STADIUM_FIELDS["Arena Name"]
        arena_name = read_utf16(gm, addr + an_off, an_len) if an_unicode else ""
        if not arena_name:
            if i > 40:
                break
            else:
                continue
        fields = {}
        for label, (off, length, is_uni) in STADIUM_FIELDS.items():
            if is_uni:
                fields[label] = read_utf16(gm, addr + off, length)
            else:
                # ASCII fields currently unused
                fields[label] = ""
        out.append(Stadium(i, addr, fields))
    return out

# -----------------------------
# GUI
# -----------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"NBA 2K25 Roster Editor Core v{APP_VERSION}")
        self.geometry("1100x680")
        self.resizable(True, True)

        if sys.platform != "win32":
            messagebox.showerror("Unsupported", "Windows-only tool.")
            self.destroy()
            return

        self.gm = GameMemory(MODULE_NAME)
        self.connected = self.gm.open()

        self._build_ui()

    # UI layout
    def _build_ui(self):
        container = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        container.pack(fill=tk.BOTH, expand=True)

        # Sidebar
        sidebar = ttk.Frame(container, width=220)
        container.add(sidebar, weight=0)

        main = ttk.Notebook(container)
        container.add(main, weight=1)

        # Home tab
        self.tab_home = ttk.Frame(main)
        main.add(self.tab_home, text="Home")
        self._build_home(self.tab_home)

        # Players tab
        self.tab_players = ttk.Frame(main)
        main.add(self.tab_players, text="Players")
        self._build_players(self.tab_players)

        # Teams tab
        self.tab_teams = ttk.Frame(main)
        main.add(self.tab_teams, text="Teams")
        self._build_teams(self.tab_teams)

        # Stadiums tab
        self.tab_stadiums = ttk.Frame(main)
        main.add(self.tab_stadiums, text="Stadiums")
        self._build_stadiums(self.tab_stadiums)

    # Home
    def _build_home(self, parent):
        status = ttk.Label(parent, text=self._status_text(), font=("Segoe UI", 12))
        status.pack(anchor="w", padx=12, pady=12)

        refresh_btn = ttk.Button(parent, text="Reconnect", command=self._reconnect)
        refresh_btn.pack(anchor="w", padx=12, pady=4)

        info = ttk.Label(parent, text=(
            "Offline = EAC disabled. Editor reads/writes live memory.\n"
            "Run game as normal (no internet required). Run editor as Administrator.\n"
            "No save-file parsing. No network calls."
        ))
        info.pack(anchor="w", padx=12, pady=12)

    def _status_text(self) -> str:
        return f"Process: {'connected' if self.connected else 'not found'}  |  Module: {MODULE_NAME}"

    def _reconnect(self):
        self.gm.close()
        self.connected = self.gm.open()
        for child in self.tab_home.winfo_children():
            child.destroy()
        self._build_home(self.tab_home)
        self._refresh_players()
        self._refresh_teams()
        self._refresh_stadiums()

    # Players tab
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

        # Editor
        editor = ttk.LabelFrame(parent, text="Edit Player")
        editor.pack(fill=tk.X, padx=8, pady=8)
        self.ep_first = tk.StringVar()
        self.ep_last = tk.StringVar()
        self.ep_face = tk.IntVar()

        ttk.Label(editor, text="First Name").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        tk.Entry(editor, textvariable=self.ep_first, width=24).grid(row=0, column=1, padx=6, pady=4)
        ttk.Label(editor, text="Last Name").grid(row=0, column=2, sticky="w", padx=6, pady=4)
        tk.Entry(editor, textvariable=self.ep_last, width=24).grid(row=0, column=3, padx=6, pady=4)
        ttk.Label(editor, text="Face ID").grid(row=0, column=4, sticky="w", padx=6, pady=4)
        tk.Entry(editor, textvariable=self.ep_face, width=10).grid(row=0, column=5, padx=6, pady=4)
        ttk.Button(editor, text="Save", command=self._save_player).grid(row=0, column=6, padx=10, pady=4)

        self.players: List[Player] = []
        self.selected_player_addr: Optional[int] = None

        self._refresh_players()

    def _refresh_players(self):
        self.players = list_players(self.gm) if self.connected else []
        for i in self.player_tree.get_children():
            self.player_tree.delete(i)
        for p in self.players:
            self.player_tree.insert("", tk.END, values=(p.index, p.first, p.last, p.face_id, p.team_name))

    def _filter_players(self):
        q = (self.player_search.get() or "").lower()
        for i in self.player_tree.get_children():
            self.player_tree.delete(i)
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
        self.ep_first.set(p.first)
        self.ep_last.set(p.last)
        self.ep_face.set(p.face_id)
        # Compute address
        base = resolve_table_base(self.gm, PLAYER_PTR_CHAINS)
        if base:
            self.selected_player_addr = base + p.index * PLAYER_STRIDE
        else:
            self.selected_player_addr = None

    def _save_player(self):
        if not self.connected or not self.selected_player_addr:
            messagebox.showwarning("Not connected", "Game not detected or player not selected.")
            return
        ok1 = write_utf16(self.gm, self.selected_player_addr + OFF_FIRST_NAME, NAME_MAX_CHARS, self.ep_first.get())
        ok2 = write_utf16(self.gm, self.selected_player_addr + OFF_LAST_NAME, NAME_MAX_CHARS, self.ep_last.get())
        ok3 = write_u32(self.gm, self.selected_player_addr + OFF_FACE_ID, int(self.ep_face.get()))
        if ok1 and ok2 and ok3:
            messagebox.showinfo("Saved", "Player updated.")
            self._refresh_players()
        else:
            messagebox.showerror("Error", "Write failed. Try running as Administrator.")

    # Teams tab
    def _build_teams(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(toolbar, text="Refresh", command=self._refresh_teams).pack(side=tk.LEFT, padx=4)

        cols = ("index", "name", "city", "city_short", "state_short", "arena")
        self.team_tree = ttk.Treeview(parent, columns=cols, show="headings", height=16)
        headers = ["Index", "Team Name", "City", "City Short", "State Short", "Arena Name"]
        for c, h in zip(cols, headers):
            self.team_tree.heading(c, text=h)
            self.team_tree.column(c, width=160 if c not in ("index","city_short","state_short") else 100, anchor=tk.W)
        self.team_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.team_tree.bind("<<TreeviewSelect>>", self._on_team_select)

        editor = ttk.LabelFrame(parent, text="Edit Team")
        editor.pack(fill=tk.X, padx=8, pady=8)
        self.t_name = tk.StringVar()
        self.t_city = tk.StringVar()
        self.t_citys = tk.StringVar()
        self.t_states = tk.StringVar()
        self.t_arena = tk.StringVar()

        labels = ["Team Name","City Name","City Short","State Short","Arena Name"]
        vars_ = [self.t_name,self.t_city,self.t_citys,self.t_states,self.t_arena]
        offs_ = [(0,0),(0,2),(1,0),(1,2),(2,0)]
        for (r,c), lab, var in zip(offs_, labels, vars_):
            ttk.Label(editor, text=lab).grid(row=r, column=c, sticky="w", padx=6, pady=4)
            tk.Entry(editor, textvariable=var, width=28).grid(row=r, column=c+1, padx=6, pady=4)

        ttk.Button(editor, text="Save", command=self._save_team).grid(row=0, column=4, padx=10, pady=4)

        self.teams: List[Team] = []
        self.selected_team_addr: Optional[int] = None

        self._refresh_teams()

    def _refresh_teams(self):
        self.teams = list_teams(self.gm) if self.connected else []
        for i in self.team_tree.get_children():
            self.team_tree.delete(i)
        for t in self.teams:
            self.team_tree.insert("", tk.END, values=(t.index, t.name, t.city, t.city_short, t.state_short, t.arena_name))

    def _on_team_select(self, _evt=None):
        sel = self.team_tree.selection()
        if not sel:
            return
        vals = self.team_tree.item(sel[0], "values")
        idx = int(vals[0])
        t = next((x for x in self.teams if x.index == idx), None)
        if not t:
            return
        self.t_name.set(t.name)
        self.t_city.set(t.city)
        self.t_citys.set(t.city_short)
        self.t_states.set(t.state_short)
        self.t_arena.set(t.arena_name)
        base = resolve_table_base(self.gm, TEAM_PTR_CHAINS)
        if base:
            self.selected_team_addr = base + t.index * TEAM_STRIDE
        else:
            self.selected_team_addr = None

    def _save_team(self):
        if not self.connected or not self.selected_team_addr:
            messagebox.showwarning("Not connected", "Game not detected or team not selected.")
            return
        ok = True
        for label, (off, maxc) in TEAM_FIELDS.items():
            val = {
                "Team Name": self.t_name.get(),
                "City Name": self.t_city.get(),
                "City Short Name": self.t_citys.get(),
                "State Short Name": self.t_states.get(),
                "Arena Name": self.t_arena.get(),
            }[label]
            ok = ok and write_utf16(self.gm, self.selected_team_addr + off, maxc, val)
        if ok:
            messagebox.showinfo("Saved", "Team updated.")
            self._refresh_teams()
        else:
            messagebox.showerror("Error", "Write failed. Try running as Administrator.")

    # Stadiums tab
    def _build_stadiums(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(toolbar, text="Refresh", command=self._refresh_stadiums).pack(side=tk.LEFT, padx=4)

        cols = ("index","arena","city","nickname")
        self.stadium_tree = ttk.Treeview(parent, columns=cols, show="headings", height=14)
        self.stadium_tree.heading("index", text="Index")
        self.stadium_tree.heading("arena", text="Arena Name")
        self.stadium_tree.heading("city", text="City")
        self.stadium_tree.heading("nickname", text="Nickname")
        for c in cols:
            self.stadium_tree.column(c, width=220 if c!="index" else 70, anchor=tk.W)
        self.stadium_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.stadium_tree.bind("<<TreeviewSelect>>", self._on_stadium_select)

        editor = ttk.LabelFrame(parent, text="Edit Stadium")
        editor.pack(fill=tk.X, padx=8, pady=8)

        # Map of field var objects for fields we expose
        self.s_vars = {k: tk.StringVar() for k in STADIUM_FIELDS.keys()}
        # render in grid, two columns
        r = 0
        for i, (label, (_off, _len, _uni)) in enumerate(STADIUM_FIELDS.items()):
            ttk.Label(editor, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=3)
            tk.Entry(editor, textvariable=self.s_vars[label], width=40).grid(row=r, column=1, padx=6, pady=3)
            r += 1
        ttk.Button(editor, text="Save", command=self._save_stadium).grid(row=0, column=2, padx=10, pady=6, rowspan=2)

        self.stadiums: List[Stadium] = []
        self.selected_stadium_addr: Optional[int] = None

        self._refresh_stadiums()

    def _refresh_stadiums(self):
        self.stadiums = list_stadiums(self.gm) if self.connected else []
        for i in self.stadium_tree.get_children():
            self.stadium_tree.delete(i)
        for s in self.stadiums:
            arena = s.fields.get("Arena Name","")
            city  = s.fields.get("City Name","")
            nick  = s.fields.get("Nickname","")
            self.stadium_tree.insert("", tk.END, values=(s.index, arena, city, nick))

    def _on_stadium_select(self, _evt=None):
        sel = self.stadium_tree.selection()
        if not sel:
            return
        vals = self.stadium_tree.item(sel[0], "values")
        idx = int(vals[0])
        s = next((x for x in self.stadiums if x.index == idx), None)
        if not s:
            return
        for label in STADIUM_FIELDS.keys():
            self.s_vars[label].set(s.fields.get(label, ""))
        base = resolve_table_base(self.gm, STADIUM_PTR_CHAINS)
        if base:
            self.selected_stadium_addr = base + s.index * STADIUM_STRIDE
        else:
            self.selected_stadium_addr = None

    def _save_stadium(self):
        if not self.connected or not self.selected_stadium_addr:
            messagebox.showwarning("Not connected", "Game not detected or stadium not selected.")
            return
        ok = True
        for label, (off, length, is_uni) in STADIUM_FIELDS.items():
            if not is_uni:
                continue
            val = self.s_vars[label].get()
            ok = ok and write_utf16(self.gm, self.selected_stadium_addr + off, length, val)
        if ok:
            messagebox.showinfo("Saved", "Stadium updated.")
            self._refresh_stadiums()
        else:
            messagebox.showerror("Error", "Write failed. Try running as Administrator.")

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
