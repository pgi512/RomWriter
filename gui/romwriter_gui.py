#!/usr/bin/env python3
"""AT28C64 ROM Writer GUI

Arduino Pro Micro 프로그래머(ROMWriter.ino v2.0)와 시리얼(115200)로 통신해서
.hex(Logisim) / .ihx(Intel HEX) / .bin 파일을 EEPROM에 굽고, 읽고, 비교하고, 바이트 단위로 편집하는 PC 프로그램.

실행: run.bat 더블클릭, 또는  python romwriter_gui.py   (필요: Python 3, pyserial)

화면 구성
  * 위쪽 모드 버튼  : 읽기(하늘) / 쓰기(복숭아) / 편집(보라) / 도구(민트). 모드마다 색이 다르다.
  * 가운데 상태 띠  : 지금 무엇을 하는 중인지(읽는 중/쓰는 중/검증 중/지우는 중)를 색과 글자로 크게 표시.
  * 아래 데이터 보기 : 16진 편집기. 바이트를 클릭하고 16진수를 입력하면 수정되고, EEPROM에 아직 안 쓴 변경은 노란색.
"""
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import types
import zlib
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import serial
import serial.tools.list_ports

from romwriter_core import (EEPROM_SIZE, Cancelled, DeviceError, FormatError, Link, compare,
                            image_to_records, load_image, write_dump_file)

APP_NAME = "ROM Writer"
APP_VERSION = "1.1"
HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller 로 묶은 .exe 에서는 번들 리소스가 sys._MEIPASS 에 풀리고, 실행 파일 옆에는 아무것도 쓰지 않는다.
ASSETS = os.path.join(getattr(sys, "_MEIPASS", HERE), "assets")
_BUILD = os.path.normpath(os.path.join(HERE, "..", "..", "build"))
DEFAULT_DIR = _BUILD if os.path.isdir(_BUILD) else os.path.expanduser("~")
SETTINGS = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ROMWriterGUI", "settings.json")


# ============================================================ 색상 테마 (라이트 / 다크, 둘 다 파스텔 톤)
def shade(color, f):
    """f<1 이면 어둡게, f>1 이면 흰색 쪽으로 밝게."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    if f < 1:
        r, g, b = int(r * f), int(g * f), int(b * f)
    else:
        r, g, b = (int(c + (255 - c) * (f - 1)) for c in (r, g, b))
    return f"#{min(r, 255):02X}{min(g, 255):02X}{min(b, 255):02X}"


THEMES = {
    "light": dict(
        dark=False,
        pal=dict(bg="#F6F4FA", card="#FFFFFF", line="#E3DEF0", text="#3A3850", muted="#8B87A3", faint="#C9C4DC",
                 field="#FFFFFF", tab="#ECE8F6", scroll="#DDD7EE", unk_bg="#F1EEF8",
                 dirty="#FFF0A6", dirty_fg="#5A4A00", bad="#FFB3C1", bad_fg="#8E1F3A", sel="#CDBDF2", sel_fg="#2F2459", selc="#E7DFFB",
                 addr="#9A86D6", dim="#B4AECB", err="#C23A57", warn="#9A7B00", good="#2E8B57",
                 dis_bg="#ECE9F3", dis_fg="#A9A5BD", soft="#F4F1FC", soft_line="#D5CEEA", soft_dis="#FAF9FD",
                 soft_act="#E9E2F8", soft_act_line="#BDB2E2", soft_dis_line="#E8E4F2", check_line="#CFC8E4"),
        modes=dict(read=("#A9D2F5", "#4A8BCB"), write=("#FFCBA4", "#D9803F"), edit=("#CDBDF2", "#7E5FCB"), tools=("#B5E8D0", "#3A9F76")),
        danger=("#FFB8C4", "#D04C68"),
        idle=("#EFEBF7", "#9C97B5"), ok=("#DDF4E4", "#2E8B57"), fail=("#FFDDE3", "#C23A57"),
        banner_bg=lambda c: shade(c, 1.45), trough=lambda accent: shade(accent, 1.7), hover=lambda c: shade(c, 1.65)),
    "dark": dict(
        dark=True,
        pal=dict(bg="#1D1B29", card="#282638", line="#3B3852", text="#E9E6F5", muted="#9C98B6", faint="#5C5875",
                 field="#1F1D2D", tab="#242234", scroll="#3E3B57", unk_bg="#322F46",
                 dirty="#5E5115", dirty_fg="#FFE98C", bad="#6E2B3B", bad_fg="#FFC4D0", sel="#4C4189", sel_fg="#EBE4FF", selc="#3A3367",
                 addr="#A894F0", dim="#5F5B7A", err="#FF8FA6", warn="#E8C24A", good="#7FD6A0",
                 dis_bg="#2F2D40", dis_fg="#6F6B8A", soft="#322F46", soft_line="#46425F", soft_dis="#2B293B",
                 soft_act="#3C3858", soft_act_line="#6D63A8", soft_dis_line="#383550", check_line="#5A5675"),
        modes=dict(read=("#33567D", "#8FC3F5"), write=("#7F512F", "#FFB985"), edit=("#54449A", "#C4B3FF"), tools=("#2B7357", "#8FE3C0")),
        danger=("#84303F", "#FF9DB0"),
        idle=("#2A2839", "#8D89AA"), ok=("#1F3A2B", "#82D9A5"), fail=("#44212C", "#FF93A9"),
        banner_bg=lambda c: shade(c, 0.55), trough=lambda accent: shade(accent, 0.4), hover=lambda c: shade(c, 0.6)),
}
MODE_INFO = {
    "read": dict(label="읽기", sub="EEPROM → PC", icon="⬇"),
    "write": dict(label="쓰기", sub="파일 → EEPROM", icon="⬆"),
    "edit": dict(label="편집", sub="바이트 직접 수정", icon="✎"),
    "tools": dict(label="도구", sub="지우기 · 검사", icon="⚙"),
}
# 아래 사전들은 한 번 만들어 두고 set_theme() 이 내용만 바꿔 끼운다 (다른 코드가 같은 객체를 계속 쓰므로).
PAL, MODES, DANGER, BANNER = {}, {}, {}, {}
THEME = {"name": "light", "dark": False, "hover": None, "trough": None}


def set_theme(name):
    t = THEMES[name]
    THEME.update(name=name, dark=t["dark"], hover=t["hover"], trough=t["trough"])
    PAL.clear()
    PAL.update(t["pal"])
    for key, info in MODE_INFO.items():
        MODES[key] = dict(info, color=t["modes"][key][0], dark=t["modes"][key][1])
    DANGER.clear()
    DANGER.update(color=t["danger"][0], dark=t["danger"][1])
    bg = t["banner_bg"]
    BANNER.clear()
    BANNER.update({
        "idle": t["idle"],                                   # 상태 띠: 종류 -> (바탕, 강조색)
        "read": (bg(MODES["read"]["color"]), MODES["read"]["dark"]),
        "write": (bg(MODES["write"]["color"]), MODES["write"]["dark"]),
        "edit": (bg(MODES["edit"]["color"]), MODES["edit"]["dark"]),
        "verify": (bg(MODES["tools"]["color"]), MODES["tools"]["dark"]),
        "erase": (bg(DANGER["color"]), DANGER["dark"]),
        "ok": t["ok"], "fail": t["fail"],
    })


def system_prefers_dark():
    """Windows 설정(앱 모드)이 다크인지. 알 수 없으면 라이트."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def resolve_theme(setting):
    return "dark" if setting == "dark" or (setting == "system" and system_prefers_dark()) else "light"


def set_titlebar_dark(win, dark):
    """Windows 제목 표시줄도 다크로 (메뉴 막대는 Windows 기본 모양 그대로 남는다)."""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):                      # DWMWA_USE_IMMERSIVE_DARK_MODE (구버전 Windows 10 은 19)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


set_theme("light")
SPINNER = "◐◓◑◒"
ROW_CHOICES = ("자동", "8", "16", "32", "64")          # 표 한 줄에 보여 줄 바이트 수


def enable_windows_polish():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)         # 글자가 뿌옇게 보이지 않도록
    except Exception:
        pass
    try:                                                         # 작업 표시줄에 파이썬 아이콘 대신 우리 아이콘이 보이도록
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GyuPuter.ROMWriter")
    except Exception:
        pass


def parse_hex_text(s):
    """'A5 3C 0x7F', 'a5,3c', 'A53C7F' 같은 16진수 목록 -> bytes. 하나라도 16진수가 아니면 None (그때는 문자로 취급)."""
    toks = [t for t in re.split(r"[\s,;]+", s.strip()) if t]
    if not toks:
        return None
    out = bytearray()
    for t in toks:
        h = t[2:] if t[:2].lower() == "0x" else t
        if not h or any(c not in "0123456789abcdefABCDEF" for c in h):
            return None
        if len(h) <= 2:
            out.append(int(h, 16))
        elif len(h) % 2 == 0:                    # 'A53C7F' 처럼 붙여 쓴 16진수 문자열
            out += bytes.fromhex(h)
        else:
            return None
    return bytes(out)


def hexnum(text, name):
    s = text.strip().lower().replace("0x", "").replace("_", "")
    try:
        return int(s, 16)
    except ValueError:
        raise ValueError(f"{name}: 16진수로 읽을 수 없습니다 ({text!r}). 예: 0x1F, 1F")


# ============================================================ 말풍선 / 모드 버튼
class Tip:
    """마우스를 올리면 설명을 보여 주는 작은 말풍선."""

    def __init__(self, widget, text, font):
        self.w, self.text, self.font, self.tw = widget, text, font, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tw or not self.text:
            return
        self.tw = tw = tk.Toplevel(self.w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{self.w.winfo_rootx() + 10}+{self.w.winfo_rooty() + self.w.winfo_height() + 6}")
        tk.Label(tw, text=self.text, bg="#3F3B57", fg="#FFFFFF", font=self.font, justify="left", padx=12, pady=8,
                 wraplength=460).pack()

    def _hide(self, _e=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None


class ModeButton(tk.Frame):
    """리본의 탭. 선택된 탭은 자기 색으로 채워지고, 바로 아래 도구 줄(같은 색 테두리)과 이어져 보인다."""

    def __init__(self, master, spec, command, fonts, index):
        super().__init__(master, bg=PAL["bg"], highlightthickness=2, highlightbackground=PAL["bg"], cursor="hand2")
        self.spec, self.command, self.active = spec, command, False
        self.icon = tk.Label(self, text=spec["icon"], font=fonts["icon"], fg=spec["dark"], bg=PAL["bg"])
        self.title = tk.Label(self, text=spec["label"], font=fonts["bold"], fg=PAL["muted"], bg=PAL["bg"])
        self.icon.pack(side="left", padx=(14, 4), pady=6)
        self.title.pack(side="left", padx=(0, 16), pady=6)
        for w in (self, self.icon, self.title):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", lambda e: self.paint(True))
            w.bind("<Leave>", lambda e: self.paint(False))
        Tip(self.icon, f"{spec['sub']}    (Ctrl+{index})", fonts["small"])
        Tip(self.title, f"{spec['sub']}    (Ctrl+{index})", fonts["small"])
        self.paint(False)

    def set_active(self, on):
        self.active = on
        self.paint(False)

    def paint(self, hover):
        c = self.spec["color"]
        if self.active:
            bg, icon_fg, text_fg = c, PAL["text"], PAL["text"]
        else:
            bg = THEME["hover"](c) if hover else PAL["bg"]
            icon_fg, text_fg = self.spec["dark"], PAL["text"] if hover else PAL["muted"]
        self.configure(bg=bg, highlightbackground=bg)
        self.icon.configure(bg=bg, fg=icon_fg)
        self.title.configure(bg=bg, fg=text_fg)


# ============================================================ 16진 편집기 (데이터 보기)
class HexView(ttk.Frame):
    """EEPROM 8KB 를 한 줄에 n바이트씩(8/16/32/64, 또는 창 너비에 맞춰 자동) 보여 주고, 바이트를 클릭해서 직접 수정하는 표.

    buf     : 지금 화면에 있는 값 (편집 대상)
    synced  : EEPROM 에 있다고 마지막으로 확인된 값. buf 와 다르면 '아직 안 쓴 변경'(노란색)
    bad     : 검증에서 틀린 주소 -> (기대값, 실제값)  (분홍색)
    """
    ADDR_W = 6                                  # "0000" + 공백 2칸
    SIZES = (8, 16, 32, 64)

    def __init__(self, master, fonts, on_select, on_change, mode="auto", on_notice=None):
        super().__init__(master, style="Card.TFrame")
        self.on_select, self.on_change = on_select, on_change
        self.on_notice = on_notice or (lambda title, detail="": None)
        self.buf, self.synced, self.bad, self.undo = {}, {}, {}, []
        self.sel, self.hi, self.locked = None, None, False
        self.anchor, self.area, self.dragging = None, "hex", False     # 선택 범위의 고정 끝 / 입력 칸(hex·asc)
        self.pin_lo = self.pin_hi = None            # 주소 이동 등으로 넓힌 표시 범위
        self.vlo, self.vhi = 0, 15                  # 지금 표에 그려진 범위 (render 가 정함)
        self.mode, self.n, self._fit_job = mode, 16 if mode == "auto" else int(mode), None
        self.font = fonts["mono"]
        self.text = tk.Text(self, font=self.font, wrap="none", bg=PAL["card"], fg=PAL["text"], bd=0,
                            highlightthickness=0, padx=12, pady=8, cursor="arrow", insertwidth=0, takefocus=True,
                            exportselection=False, selectbackground=PAL["card"], inactiveselectbackground=PAL["card"],
                            spacing1=1, spacing3=1)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        t = self.text
        t.tag_configure("hdr", foreground=PAL["muted"])
        t.tag_configure("addr", foreground=PAL["addr"])
        t.tag_configure("asc", foreground=PAL["muted"])
        t.tag_configure("unk", foreground=PAL["faint"])
        t.tag_configure("zero", foreground=PAL["dim"])
        t.tag_configure("ff", foreground=PAL["dim"])
        t.tag_configure("dirty", background=PAL["dirty"], foreground=PAL["dirty_fg"])
        t.tag_configure("bad", background=PAL["bad"], foreground=PAL["bad_fg"])
        t.tag_configure("sel", background=PAL["sel"], foreground=PAL["sel_fg"])
        t.tag_configure("selc", background=PAL["selc"])           # 선택한 칸의 짝(16진수 <-> 문자) 표시
        for name in ("dirty", "bad", "selc", "sel"):
            t.tag_raise(name)
        t.bind("<Button-1>", self._press)
        t.bind("<Shift-Button-1>", self._press)
        t.bind("<B1-Motion>", self._drag)
        t.bind("<ButtonRelease-1>", self._release)
        t.bind("<Key>", self._key)
        for seq, fn in (("<Control-c>", self.copy), ("<Control-C>", self.copy), ("<Control-v>", self.paste), ("<Control-V>", self.paste)):
            t.bind(seq, lambda e, f=fn: (f(), "break")[1])
        t.bind("<Configure>", self._on_resize)
        self.render()

    # ---- 한 줄 바이트 수
    @property
    def rows(self):
        return (self.vhi - self.vlo + 1) // self.n

    def _range(self, include_sel=True):
        """표에 보여 줄 주소 범위 = 데이터가 있는 곳 + 선택한 곳 + 주소 이동으로 넓힌 곳 (줄 단위로 맞춤)."""
        addrs = list(self.buf)
        if include_sel and self.sel is not None:
            addrs.append(self.sel)
        addrs += [p for p in (self.pin_lo, self.pin_hi) if p is not None]
        lo, hi = (min(addrs), max(addrs)) if addrs else (0, 0)
        n = self.n
        return lo // n * n, hi // n * n + n - 1

    def _extend(self, a):
        """표에 없는 주소로 갈 때 그 주소까지 표시 범위를 넓힌다."""
        self.pin_lo = a if self.pin_lo is None else min(self.pin_lo, a)
        self.pin_hi = a if self.pin_hi is None else max(self.pin_hi, a)
        self.render()
        self.on_change()

    def byte_col(self, i):
        """한 줄 안에서 i번째 바이트가 시작하는 글자 칸. 8바이트마다 빈칸 하나가 더 들어간다."""
        return self.ADDR_W + i * 3 + i // 8

    @property
    def ascii_col(self):
        return self.ADDR_W + self.n * 3 + (self.n - 1) // 8 + 2

    def fit_n(self):
        """지금 창 너비에 들어가는 가장 큰 한 줄 바이트 수."""
        w = self.text.winfo_width()
        if w < 200:                                  # 아직 화면에 배치되기 전
            return self.n
        avail = (w - 28) // max(self.font.measure("0"), 1)
        best = self.SIZES[0]
        for n in self.SIZES:
            if self.ADDR_W + n * 3 + (n - 1) // 8 + 2 + n + 1 <= avail:
                best = n
        return best

    def _on_resize(self, _e):
        if self.mode != "auto":
            return
        if self._fit_job:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(150, self._refit)

    def _refit(self):
        self._fit_job = None
        n = self.fit_n()
        if n != self.n:
            self._set_n(n)

    def set_row_mode(self, mode):
        """mode: 'auto' 또는 8/16/32/64."""
        self.mode = mode
        n = self.fit_n() if mode == "auto" else int(mode)
        if n != self.n:
            self._set_n(n)

    def _set_n(self, n):
        try:                                         # 보고 있던 위치(맨 위 줄의 주소)를 유지
            first = int(self.text.index("@0,0").split(".")[0]) - 2
        except (tk.TclError, ValueError):
            first = 0
        top_addr = self.vlo + max(first, 0) * self.n
        self.n = n
        self.hi = None
        self.render()
        self.text.yview_moveto(min(1.0, (max(top_addr - self.vlo, 0) // n + 1) / (self.rows + 1)))
        self.on_change()

    # ---- 좌표 <-> 주소
    def _idx(self, addr, ascii_=False):
        row, i = divmod(addr - self.vlo, self.n)
        col = self.ascii_col + i if ascii_ else self.byte_col(i)
        return f"{row + 2}.{col}"

    def _cell_text(self, a):
        if self.sel == a and self.hi is not None:
            return f"{self.hi:X}_"
        v = self.buf.get(a)
        return "--" if v is None else f"{v:02X}"

    @staticmethod
    def _ascii(v):
        return "." if v is None or not 32 <= v < 127 else chr(v)

    def is_dirty(self, a):
        return a in self.buf and self.buf[a] != self.synced.get(a)

    def dirty_addrs(self):
        return [a for a in sorted(self.buf) if self.buf[a] != self.synced.get(a)]

    def _tag_for(self, a):
        v = self.buf.get(a)
        if a in self.bad:
            return "bad"
        if v is None:
            return "unk"
        if self.is_dirty(a):
            return "dirty"
        return "zero" if v == 0 else "ff" if v == 0xFF else None

    @staticmethod
    def _sep(i, n):
        return " " + (" " if i % 8 == 7 and i != n - 1 else "")

    # ---- 그리기
    def render(self):
        t, n = self.text, self.n
        self.vlo, self.vhi = self._range()
        t.configure(state="normal")
        t.delete("1.0", "end")
        hdr = "ADDR  " + "".join(f"{i:02X}" + self._sep(i, n) for i in range(n)) + "  ASCII"
        lines = [hdr]
        tags = {}
        ac = self.ascii_col
        for r in range(self.rows):
            base = self.vlo + r * n
            cells = "".join(self._cell_text(base + i) + self._sep(i, n) for i in range(n))
            asc = "".join(self._ascii(self.buf.get(base + i)) for i in range(n))
            lines.append(f"{base:04X}  {cells}  {asc}")
            line_no = r + 2
            tags.setdefault("addr", []).extend([f"{line_no}.0", f"{line_no}.4"])
            tags.setdefault("asc", []).extend([f"{line_no}.{ac - 1}", f"{line_no}.{ac + n}"])
            for i in range(n):
                tag = self._tag_for(base + i)
                if tag:
                    c = self.byte_col(i)
                    tags.setdefault(tag, []).extend([f"{line_no}.{c}", f"{line_no}.{c + 2}"])
        t.insert("1.0", "\n".join(lines))
        t.tag_add("hdr", "1.0", "1.end")
        for tag, pairs in tags.items():
            for k in range(0, len(pairs), 400):             # 한 번에 너무 많이 넘기지 않도록 나눠서
                t.tag_add(tag, *pairs[k:k + 400])
        t.configure(state="disabled")
        self._paint_sel()

    def _retag(self, a):
        t = self.text
        t.configure(state="normal")
        s = self._idx(a)
        e = f"{s.split('.')[0]}.{int(s.split('.')[1]) + 2}"
        for tag in ("zero", "ff", "unk", "dirty", "bad"):
            t.tag_remove(tag, s, e)
        t.delete(s, e)
        t.insert(s, self._cell_text(a))
        tag = self._tag_for(a)
        if tag:
            t.tag_add(tag, s, e)
        sa = self._idx(a, True)
        t.delete(sa, f"{sa} +1c")
        t.insert(sa, self._ascii(self.buf.get(a)))
        t.configure(state="disabled")
        self._paint_sel()

    def _paint_sel(self):
        """선택한 범위를 칠한다: 입력 중인 칸(16진수 또는 문자)은 진하게, 짝이 되는 칸은 연하게."""
        t = self.text
        t.tag_remove("sel", "1.0", "end")
        t.tag_remove("selc", "1.0", "end")
        r = self.sel_range()
        if not r:
            return
        lo, hi = max(r[0], self.vlo), min(r[1], self.vhi)
        hex_pairs, asc_pairs = [], []
        for a in range(lo, hi + 1):
            s = self._idx(a)
            hex_pairs += [s, f"{s.split('.')[0]}.{int(s.split('.')[1]) + 2}"]
            sa = self._idx(a, True)
            asc_pairs += [sa, f"{sa} +1c"]
        main, soft = (hex_pairs, asc_pairs) if self.area == "hex" else (asc_pairs, hex_pairs)
        for tag, pairs in (("sel", main), ("selc", soft)):
            for k in range(0, len(pairs), 400):
                t.tag_add(tag, *pairs[k:k + 400])

    # ---- 데이터 넣기
    def load(self, buf, synced=None, bad=None, keep_undo=False):
        self.buf = dict(buf)
        self.pin_lo = self.pin_hi = None                       # 새 데이터를 불러오면 표시 범위는 데이터 크기로 되돌아간다
        lo, hi = self._range(include_sel=False)
        if self.sel is not None and not lo <= self.sel <= hi:
            self.sel = None
        self.anchor = self.sel
        if synced is not None:
            self.synced = dict(synced)
        self.bad = dict(bad or {})
        if not keep_undo:
            self.undo = []
        self.hi = None
        self.render()
        self.on_change()
        if self.sel is not None:
            self.on_select(self.sel)

    def refresh(self):
        self.render()
        self.on_change()
        if self.sel is not None:
            self.on_select(self.sel)

    # ---- 선택, 편집
    def select(self, a, see=True, extend=False):
        """a 로 커서를 옮긴다. extend=True 면 고정 끝(anchor)은 두고 범위만 넓힌다 (Shift+클릭/방향키/드래그)."""
        a = max(0, min(EEPROM_SIZE - 1, a))
        old, pending = self.sel, self.hi is not None
        self.hi = None
        if not extend or self.anchor is None:
            self.anchor = a
        self.sel = a
        if not self.vlo <= a <= self.vhi:
            self._extend(a)                                      # 표에 없는 주소: 그 주소까지 넓혀서 다시 그림
        else:
            if pending and old is not None and self.vlo <= old <= self.vhi:
                self._retag(old)                                 # 입력하다 만 'A_' 표시를 원래대로
            else:
                self._paint_sel()
        if see:
            self.text.see(self._idx(a))
        self.on_select(a)

    def _restore(self, items):
        """[(주소, 이전 값 또는 None), ...] 로 되돌린다."""
        for a, old in reversed(items):
            if old is None:
                self.buf.pop(a, None)
            else:
                self.buf[a] = old

    def _redraw(self, addrs):
        """바뀐 주소들만 다시 그리고, 많거나 범위 밖이면 표 전체를 다시 그린다."""
        lo, hi = min(addrs), max(addrs)
        if lo < self.vlo or hi > self.vhi:
            self.pin_lo = lo if self.pin_lo is None else min(self.pin_lo, lo)
            self.pin_hi = hi if self.pin_hi is None else max(self.pin_hi, hi)
            self.render()
        elif len(addrs) > 64:
            self.render()
        else:
            for a in addrs:
                self._retag(a)
        self.on_change()

    def set_byte(self, a, v, undo=True):
        return self.set_bytes(a, bytes([v]), undo)

    def set_bytes(self, start, data, undo=True):
        """start 주소부터 data 를 쓴다. 한 번의 되돌리기(Ctrl+Z)로 전부 되돌아가도록 묶어서 기록한다."""
        group, changed = [], []
        for i, b in enumerate(data):
            a = start + i
            if a >= EEPROM_SIZE:
                break
            old = self.buf.get(a)
            if old == b and a not in self.bad:
                continue
            group.append((a, old))
            self.buf[a] = b
            self.bad.pop(a, None)
            changed.append(a)
        if not changed:
            return 0
        if undo:
            self.undo.append(group[0] if len(group) == 1 else group)
        self.hi = None
        self._redraw(changed)
        if self.sel is not None:
            self.on_select(self.sel)
        return len(changed)

    def undo_last(self):
        if not self.undo or self.locked:
            return
        entry = self.undo.pop()
        items = entry if isinstance(entry, list) else [entry]
        self._restore(items)
        self.hi = None
        self._redraw([a for a, _ in items])
        self.select(items[0][0])

    def revert_all(self):
        self.buf = {a: v for a, v in self.synced.items()}
        self.undo, self.bad = [], {}
        self.render()
        self.on_change()
        if self.sel is not None:
            self.on_select(self.sel)

    # ---- 선택 범위, 복사 / 붙여넣기
    def sel_range(self):
        if self.sel is None:
            return None
        a = self.anchor if self.anchor is not None else self.sel
        return min(a, self.sel), max(a, self.sel)

    def copy(self, auto=False):
        """선택한 범위를 클립보드로. 16진수 칸에서 선택했으면 'A5 3C ..', 문자 칸이면 글자로."""
        r = self.sel_range()
        data = [self.buf[a] for a in range(r[0], r[1] + 1) if a in self.buf] if r else []
        if not data:
            self.on_notice("복사할 값이 없습니다", "선택한 칸에 아직 값이 없습니다 (-- 칸).")
            return False
        if self.area == "hex":
            text, kind = " ".join(f"{b:02X}" for b in data), "16진수"
        else:
            text, kind = "".join(chr(b) if 32 <= b < 127 else "." for b in data), "문자"
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.on_notice("클립보드에 복사했습니다" + (" (드래그)" if auto else ""),
                       f"{len(data)}바이트 · {kind} 형식 · 0x{r[0]:04X}~0x{r[1]:04X}")
        return True

    def paste(self):
        if self.locked:
            return False
        r = self.sel_range()
        if r is None:
            self.on_notice("붙여넣을 위치가 없습니다", "표에서 붙여넣을 주소를 먼저 클릭하세요.")
            return False
        try:
            s = self.clipboard_get()
        except tk.TclError:
            self.on_notice("붙여넣을 내용이 없습니다", "클립보드에 텍스트가 없습니다.")
            return False
        data, kind = (parse_hex_text(s) if self.area == "hex" else None), "16진수"
        if data is None:
            data, kind = s.replace("\r\n", "\n").encode("utf-8"), "문자(UTF-8)"
        if not data:
            return False
        start, cut = r[0], ""
        if start + len(data) > EEPROM_SIZE:
            data, cut = data[:EEPROM_SIZE - start], f" (끝 주소를 넘는 {start + len(data) - EEPROM_SIZE}바이트는 뺐습니다)"
        n = self.set_bytes(start, data)
        self.select(start, see=False)
        self.select(start + len(data) - 1, see=False, extend=True)
        self.text.see(self._idx(start))
        self.on_notice("붙여넣었습니다", f"{len(data)}바이트 · {kind} 해석 · 0x{start:04X}~0x{start + len(data) - 1:04X}"
                       f" (바뀐 값 {n}개){cut}")
        return True

    # ---- 마우스
    def _addr_at(self, x, y):
        line, col = (int(v) for v in self.text.index(f"@{x},{y}").split("."))
        row = min(max(line - 2, 0), self.rows - 1)
        n = self.n
        if col >= self.ascii_col - 1:
            return self.vlo + row * n + min(max(col - self.ascii_col, 0), n - 1), "asc"
        i = 0
        for k in range(n):
            if self.byte_col(k) <= col:
                i = k
        return self.vlo + row * n + i, "hex"

    def _press(self, e):
        self.text.focus_set()
        a, area = self._addr_at(e.x, e.y)
        self.area = area
        self.dragging = True
        self.select(a, see=False, extend=bool(e.state & 0x1))        # Shift+클릭은 범위를 넓힘
        return "break"

    def _drag(self, e):
        if not self.dragging:
            return "break"
        h = self.text.winfo_height()
        if e.y < 0:
            self.text.yview_scroll(-1, "units")
        elif e.y > h:
            self.text.yview_scroll(1, "units")
        a, _ = self._addr_at(e.x, min(max(e.y, 0), h - 1))
        if a != self.sel:
            self.select(a, see=False, extend=True)
        return "break"

    def _release(self, _e):
        if not self.dragging:
            return "break"
        self.dragging = False
        r = self.sel_range()
        if r and r[0] != r[1]:
            self.copy(auto=True)                  # 드래그가 끝나면 바로 클립보드로
        return "break"

    # ---- 키보드
    def _key(self, e):
        if e.state & 0x4:                       # Ctrl 조합(복사/붙여넣기/되돌리기 등)은 따로 처리
            return None
        a, n = self.sel, self.n
        if a is None:
            return "break"
        shift = bool(e.state & 0x1)
        moves = {"Right": 1, "Left": -1, "Down": n, "Up": -n, "Next": n * 16, "Prior": -n * 16, "Tab": 1}
        if e.keysym in moves:
            self.select(a + moves[e.keysym], extend=shift)
        elif e.keysym == "Home":
            self.select(a // n * n, extend=shift)
        elif e.keysym == "End":
            self.select(a // n * n + n - 1, extend=shift)
        elif e.keysym == "F2":                  # 16진수 칸 <-> 문자 칸 전환
            self.area = "asc" if self.area == "hex" else "hex"
            self.hi = None
            self._retag(a)
            self.on_select(a)
        elif e.keysym in ("BackSpace", "Escape") and self.hi is not None:
            self.hi = None
            self._retag(a)
        elif e.keysym == "BackSpace" and self.area == "asc":
            self.select(a - 1)
        elif self.locked:
            pass
        elif self.area == "asc":
            ch = e.char
            if ch and ch.isprintable() and ord(ch) >= 32 and ord(ch) != 127:
                data = ch.encode("utf-8")        # 영문은 1바이트, 한글 등은 UTF-8 여러 바이트
                self.set_bytes(a, data)
                self.select(a + len(data))
        elif e.char and e.char in "0123456789abcdefABCDEF":
            d = int(e.char, 16)
            if self.hi is None:
                self.hi = d
                self._retag(a)
            else:
                v = (self.hi << 4) | d
                self.hi = None
                self.set_byte(a, v)
                self.select(a + 1)
        return "break"


# ============================================================ 작업 스레드에서 쓰는 도우미
class Ctx:
    def __init__(self, app):
        self.app = app

    def phase(self, kind, title):
        self.app.post("phase", kind, title)

    def progress(self, cur, tot):
        self.app.post("progress", cur, tot)

    def apply(self, fn):
        """화면 상태를 바꾸는 일은 메인 스레드에서 하도록 넘긴다."""
        self.app.post("apply", fn)


# ============================================================ 앱
class App(tk.Tk):
    def __init__(self):
        enable_windows_polish()
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1120x780")
        self.minsize(920, 600)
        self.configure(bg=PAL["bg"])
        self.quiet = False                      # 자동 테스트에서 팝업을 끄는 용도
        self.q = queue.Queue()
        self.link = Link(lambda s: self.q.put(("log", s)))
        self.busy, self.cancelable = False, True
        self.cancel_evt = threading.Event()
        self.file_raw = None                    # 파일에서 읽은 {주소: 바이트} (오프셋 적용 전)
        self.file_name = ""
        self.last_result = None
        self.mode = None
        self.chip_btns, self.local_btns, self.mode_btns, self.panels = [], [], {}, {}
        self.spin_i, self.phase_t0, self.kind = 0, 0.0, "idle"
        self.settings = self._load_settings()
        self.theme_var = tk.StringVar(value=self.settings.get("theme", "system"))     # light / dark / system
        rm = self.settings.get("row_mode", "자동")
        self.row_var = tk.StringVar(value=rm if rm in ROW_CHOICES else "자동")           # 표 한 줄 바이트 수
        set_theme(resolve_theme(self.theme_var.get()))
        self.configure(bg=PAL["bg"])
        self.conn_port, self._banner_args, self._view_state = "", ("idle", "대기 중", "", ""), None
        self._fonts()
        self._icon()
        self._styles()
        self._build()
        self._menu()
        set_titlebar_dark(self, THEME["dark"])
        self.refresh_ports()
        self.show_mode("read")
        self.refresh_enabled()                 # 연결 전에는 EEPROM을 건드리는 버튼을 꺼 둔다
        self.set_banner("idle", "대기 중", "연결 후 모드를 골라 작업하세요.")
        self.bind_all("<F5>", lambda e: self.start_read())
        self.bind_all("<Control-o>", lambda e: self.choose_file())
        self.bind_all("<Control-s>", lambda e: self.save_buffer())
        self.bind_all("<Control-g>", lambda e: self.goto_entry.focus_set())
        for i, key in enumerate(MODES, 1):
            self.bind_all(f"<Control-Key-{i}>", lambda e, k=key: self.show_mode(k))
        self.bind_all("<Control-z>", lambda e: self.hv.undo_last() if self.focus_get() is self.hv.text else None)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll)
        self.after(120, self._tick)

    # ---------------------------------------------------------- 설정 / 글꼴 / 아이콘 / 스타일
    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def _fonts(self):
        fams = set(tkfont.families())
        ui = next((f for f in ("Malgun Gothic", "맑은 고딕", "Segoe UI", "Arial") if f in fams), "TkDefaultFont")
        mono = next((f for f in ("Consolas", "D2Coding", "Cascadia Mono", "Courier New") if f in fams), "TkFixedFont")
        F = tkfont.Font
        self.fonts = dict(
            ui=F(family=ui, size=10), small=F(family=ui, size=9), bold=F(family=ui, size=10, weight="bold"),
            title=F(family=ui, size=13, weight="bold"), big=F(family=ui, size=18, weight="bold"),
            icon=F(family="Segoe UI Symbol" if "Segoe UI Symbol" in fams else ui, size=20),
            mono=F(family=mono, size=11), monobig=F(family=mono, size=18, weight="bold"))

    def _icon(self):
        # 제목 표시줄 왼쪽 위 / 작업 표시줄 / Alt+Tab 아이콘. .ico 가 크기별(16~256)로 들어 있어서 선명하다.
        # PhotoImage 는 변수에 담아 두지 않으면 파이썬이 지워 버려서 아이콘이 사라진다.
        try:
            self.iconbitmap(os.path.join(ASSETS, "romwriter.ico"))
        except tk.TclError:
            pass
        try:
            self._icon_img = tk.PhotoImage(file=os.path.join(ASSETS, "romwriter_256.png"))
            if sys.platform != "win32":
                self.iconphoto(True, self._icon_img)
        except tk.TclError:
            pass
        try:
            self.logo = tk.PhotoImage(file=os.path.join(ASSETS, "romwriter_64.png"))
        except tk.TclError:
            self.logo = None

    def _styles(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        P, f, dark = PAL, self.fonts, THEME["dark"]
        st.configure(".", background=P["bg"], foreground=P["text"], font=f["ui"], bordercolor=P["line"],
                     focuscolor=P["bg"], lightcolor=P["bg"], darkcolor=P["bg"])
        st.configure("TFrame", background=P["bg"])
        st.configure("Card.TFrame", background=P["card"])
        st.configure("TLabel", background=P["bg"], foreground=P["text"])
        st.configure("Card.TLabel", background=P["card"], foreground=P["text"])
        st.configure("Muted.TLabel", background=P["card"], foreground=P["muted"], font=f["small"])
        st.configure("Hint.TLabel", background=P["card"], foreground=P["muted"], font=f["small"])
        st.configure("TCheckbutton", background=P["card"], foreground=P["text"], indicatorcolor=P["field"],
                     upperbordercolor=P["check_line"], lowerbordercolor=P["check_line"], focuscolor=P["card"])
        st.map("TCheckbutton", indicatorcolor=[("selected", MODES["write"]["dark"])], background=[("active", P["card"])])
        st.configure("TEntry", fieldbackground=P["field"], foreground=P["text"], insertcolor=P["text"],
                     bordercolor=P["line"], lightcolor=P["line"], darkcolor=P["line"], padding=5)
        st.configure("TCombobox", fieldbackground=P["field"], background=P["field"], foreground=P["text"],
                     bordercolor=P["line"], lightcolor=P["line"], darkcolor=P["line"], arrowcolor=P["muted"], padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", P["field"])], selectbackground=[("readonly", P["field"])],
               selectforeground=[("readonly", P["text"])], foreground=[("readonly", P["text"])])
        for opt, val in (("background", P["field"]), ("foreground", P["text"]),
                         ("selectBackground", P["sel"]), ("selectForeground", P["sel_fg"])):
            self.option_add(f"*TCombobox*Listbox.{opt}", val)         # 콤보박스 펼침 목록 색
        for key, spec in list(MODES.items()) + [("danger", DANGER)]:
            st.configure(f"{key}.TButton", background=spec["color"], foreground=P["text"], borderwidth=0,
                         focusthickness=0, padding=(16, 8), font=f["bold"], relief="flat")
            st.map(f"{key}.TButton", background=[("disabled", P["dis_bg"]), ("active", shade(spec["color"], 1.2 if dark else 0.93))],
                   foreground=[("disabled", P["dis_fg"])])
        st.configure("Soft.TButton", background=P["soft"], foreground=P["text"], bordercolor=P["soft_line"], borderwidth=1,
                     lightcolor=P["soft"], darkcolor=P["soft"], padding=(12, 7), focusthickness=0, relief="flat")
        st.map("Soft.TButton", background=[("disabled", P["soft_dis"]), ("active", P["soft_act"])],
               bordercolor=[("disabled", P["soft_dis_line"]), ("active", P["soft_act_line"])],
               foreground=[("disabled", P["dis_fg"])])
        for kind, (bg, accent) in BANNER.items():
            trough = bg if kind == "idle" else THEME["trough"](accent)         # 대기 중에는 막대가 보이지 않게
            st.configure(f"{kind}.Horizontal.TProgressbar", troughcolor=trough, background=accent,
                         bordercolor=bg, lightcolor=accent, darkcolor=accent, thickness=6)
        st.configure("TNotebook", background=P["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=P["tab"], foreground=P["muted"], padding=(18, 7), borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", P["card"])], foreground=[("selected", P["text"])])
        st.configure("Vertical.TScrollbar", background=P["scroll"], troughcolor=P["card"], bordercolor=P["card"],
                     arrowcolor=P["muted"], relief="flat")

    # ---------------------------------------------------------- 화면 구성
    def _btn(self, parent, text, kind, command, chip=False):
        b = ttk.Button(parent, text=text, style=f"{kind}.TButton", command=command)
        (self.chip_btns if chip else self.local_btns).append(b)
        return b

    def _card(self, parent, accent=None, pad=(12, 8)):
        f = tk.Frame(parent, bg=PAL["card"], highlightthickness=2 if accent else 1,
                     highlightbackground=accent or PAL["line"])
        inner = ttk.Frame(f, style="Card.TFrame", padding=pad)
        inner.pack(fill="both", expand=True)
        return f, inner

    def _tip_icon(self, parent, text, row, column):
        """패널 오른쪽 끝의 ⓘ. 마우스를 올리면 설명이 뜬다 (화면 공간을 아끼려고 안내 문구를 여기로 옮김)."""
        lb = tk.Label(parent, text="ⓘ", font=self.fonts["bold"], bg=PAL["card"], fg=PAL["muted"], cursor="question_arrow")
        lb.grid(row=row, column=column, sticky="e", padx=(12, 0))
        Tip(lb, text, self.fonts["small"])
        return lb

    def _build(self):
        root = ttk.Frame(self, padding=(12, 8, 12, 10))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)                      # 데이터 보기가 남는 공간을 전부 가져간다

        # --- 리본: 맨 위에 모드 탭, 바로 아래에 붙어 있는 모드별 도구 줄 (엑셀 리본처럼)
        bar = ttk.Frame(root)
        bar.grid(row=0, column=0, sticky="w")
        for i, (key, spec) in enumerate(MODES.items(), 1):
            b = ModeButton(bar, spec, lambda k=key: self.show_mode(k), self.fonts, i)
            b.grid(row=0, column=i - 1, padx=(0, 2))
            self.mode_btns[key] = b

        # --- 모드별 한 줄 패널 (같은 자리에 겹쳐 두고 하나만 보여 줌)
        self.panel_host = ttk.Frame(root)
        self.panel_host.grid(row=1, column=0, sticky="ew")
        self.panel_host.columnconfigure(0, weight=1)
        self.panel_host.rowconfigure(0, weight=1)
        self._panel_read()
        self._panel_write()
        self._panel_edit()
        self._panel_tools()

        # --- 연결 줄 (왼쪽 정렬)
        conn = ttk.Frame(root)
        conn.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(conn, textvariable=self.port_var, width=34)
        self.port_box.grid(row=0, column=0, padx=(0, 4))
        ttk.Button(conn, text="새로고침", style="Soft.TButton", command=self.refresh_ports).grid(row=0, column=1, padx=4)
        self.conn_btn = ttk.Button(conn, text="연결", style="read.TButton", command=self.toggle_connect)
        self.conn_btn.grid(row=0, column=2, padx=4)
        self.pill = tk.Label(conn, text="●  연결 안 됨", font=self.fonts["bold"], bg=BANNER["fail"][0], fg=BANNER["fail"][1], padx=10, pady=4)
        self.pill.grid(row=0, column=3, padx=(8, 0))

        # --- 상태 띠 (한 줄 + 얇은 진행 막대)
        self.banner = tk.Frame(root, highlightthickness=1)
        self.banner.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.banner.columnconfigure(2, weight=1)
        self.b_icon = tk.Label(self.banner, font=self.fonts["bold"], width=2)
        self.b_icon.grid(row=0, column=0, padx=(10, 2), pady=(6, 2))
        self.b_title = tk.Label(self.banner, font=self.fonts["bold"], anchor="w")
        self.b_title.grid(row=0, column=1, sticky="w", pady=(6, 2))
        self.b_detail = tk.Label(self.banner, font=self.fonts["ui"], anchor="w", justify="left", wraplength=900)
        self.b_detail.grid(row=0, column=2, sticky="w", padx=(12, 0), pady=(6, 2))
        self.b_right = tk.Label(self.banner, font=self.fonts["bold"], anchor="e")
        self.b_right.grid(row=0, column=3, sticky="e", padx=(8, 8), pady=(6, 2))
        self.cancel_btn = ttk.Button(self.banner, text="취소", style="Soft.TButton", command=self.cancel_evt.set)
        self.cancel_btn.grid(row=0, column=4, padx=(0, 8), pady=(4, 2))
        self.progress = ttk.Progressbar(self.banner, mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 7))

        # --- 데이터 보기 (가장 크게) + 선택한 바이트
        work = ttk.Frame(root)
        work.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        work.columnconfigure(1, weight=1)
        work.rowconfigure(0, weight=1)
        self.nb = nb = ttk.Notebook(work)
        nb.grid(row=0, column=1, sticky="nsew")
        self.data_tab = data_tab = ttk.Frame(nb, style="Card.TFrame")
        self.log_tab = log_tab = ttk.Frame(nb, style="Card.TFrame")
        nb.add(data_tab, text="  데이터 보기  ")
        nb.add(log_tab, text="  통신 로그  ")
        data_tab.columnconfigure(0, weight=1)
        data_tab.rowconfigure(1, weight=1)
        top = ttk.Frame(data_tab, style="Card.TFrame", padding=(12, 6, 12, 0))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.view_title = tk.Label(top, text="아직 표시할 데이터가 없습니다. 읽기나 파일 열기로 시작하세요.", bg=PAL["card"], fg=PAL["muted"],
                                   font=self.fonts["bold"], anchor="w")
        self.view_title.grid(row=0, column=0, sticky="w")
        legend = ttk.Frame(top, style="Card.TFrame")
        legend.grid(row=0, column=1, sticky="e", padx=(8, 14))
        for text, bg, fg in (("선택", PAL["sel"], PAL["sel_fg"]), ("아직 안 쓴 변경", PAL["dirty"], PAL["dirty_fg"]),
                             ("검증 불일치", PAL["bad"], PAL["bad_fg"]), ("-- 모름", PAL["unk_bg"], PAL["muted"])):
            tk.Label(legend, text=f" {text} ", bg=bg, fg=fg, font=self.fonts["small"]).pack(side="left", padx=(0, 5))
        rs = ttk.Frame(top, style="Card.TFrame")
        rs.grid(row=0, column=2, sticky="e", padx=(0, 14))
        ttk.Label(rs, text="한 줄", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        rcb = ttk.Combobox(rs, textvariable=self.row_var, values=ROW_CHOICES, state="readonly", width=5)
        rcb.pack(side="left")
        rcb.bind("<<ComboboxSelected>>", self._row_changed)
        Tip(rcb, "표 한 줄에 보여 줄 바이트 수. '자동'은 창 너비에 맞춰 8/16/32/64 중에서 고릅니다.", self.fonts["small"])
        gt = ttk.Frame(top, style="Card.TFrame")
        gt.grid(row=0, column=3, sticky="e")
        self.range_var = tk.StringVar(value="")
        ttk.Label(gt, textvariable=self.range_var, style="Muted.TLabel").pack(side="left", padx=(0, 14))
        glabel = ttk.Label(gt, text="주소 이동", style="Muted.TLabel")
        glabel.pack(side="left", padx=(0, 6))
        Tip(glabel, "표에 없는 주소로 이동하면 그 주소까지 표시 범위가 넓어집니다. (새로 읽거나 파일을 열면 데이터 크기로 다시 줄어듭니다.)", self.fonts["small"])
        self.goto_var = tk.StringVar()
        self.goto_entry = ttk.Entry(gt, textvariable=self.goto_var, width=7)
        self.goto_entry.pack(side="left")
        self.goto_entry.bind("<Return>", lambda e: self.goto())
        ttk.Button(gt, text="이동", style="Soft.TButton", command=self.goto).pack(side="left", padx=(6, 0))
        self.hv = HexView(data_tab, self.fonts, self._on_select, self._on_change,
                          mode="auto" if self.row_var.get() == "자동" else self.row_var.get(), on_notice=self._notice)
        self.hv.grid(row=1, column=0, sticky="nsew", padx=2, pady=(6, 2))
        self.log_box = ScrolledText(log_tab, font=self.fonts["mono"], bg=PAL["card"], fg=PAL["text"], bd=0,
                                    highlightthickness=0, padx=10, pady=8, state="disabled")
        self.log_box.pack(fill="both", expand=True)
        self._inspector(work)

    # ---- 읽기
    def _panel_read(self):
        card, f = self._card(self.panel_host, MODES["read"]["color"])
        self.panels["read"] = card
        card.grid(row=0, column=0, sticky="nsew")
        f.columnconfigure(8, weight=1)
        ttk.Label(f, text="읽을 범위", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.presets = {"앞 32바이트 (컨트롤 ROM)": (0, 0x1F), "앞 256바이트": (0, 0xFF), "앞 1KB": (0, 0x3FF),
                        "전체 8KB": (0, EEPROM_SIZE - 1), "직접 입력": None}
        self.preset_var = tk.StringVar(value="앞 32바이트 (컨트롤 ROM)")
        cb = ttk.Combobox(f, textvariable=self.preset_var, values=list(self.presets), state="readonly", width=22)
        cb.grid(row=0, column=1, padx=8)
        cb.bind("<<ComboboxSelected>>", self._preset_changed)
        self.start_var, self.end_var = tk.StringVar(value="0x0000"), tk.StringVar(value="0x001F")
        ttk.Label(f, text="시작", style="Muted.TLabel").grid(row=0, column=2, padx=(10, 4))
        ttk.Entry(f, textvariable=self.start_var, width=8).grid(row=0, column=3)
        ttk.Label(f, text="끝", style="Muted.TLabel").grid(row=0, column=4, padx=(10, 4))
        ttk.Entry(f, textvariable=self.end_var, width=8).grid(row=0, column=5)
        self._btn(f, "⬇  읽기  (F5)", "read", self.start_read, chip=True).grid(row=0, column=6, padx=(16, 6))
        self._btn(f, "읽은 내용 저장…", "Soft", self.save_buffer).grid(row=0, column=7, padx=2)
        self._tip_icon(f, "EEPROM에서 값을 읽어 아래 표에 보여 줍니다.\n읽기는 EEPROM 내용을 바꾸지 않습니다.", 0, 8)

    def _preset_changed(self, _e=None):
        r = self.presets.get(self.preset_var.get())
        if r:
            self.start_var.set(f"0x{r[0]:04X}")
            self.end_var.set(f"0x{r[1]:04X}")

    # ---- 쓰기
    def _panel_write(self):
        card, f = self._card(self.panel_host, MODES["write"]["color"])
        self.panels["write"] = card
        card.grid(row=0, column=0, sticky="nsew")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="파일", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.file_var = tk.StringVar()
        self.file_box = ttk.Combobox(f, textvariable=self.file_var, values=self.settings.get("recent", []))
        self.file_box.grid(row=0, column=1, sticky="ew", padx=8)
        self.file_box.bind("<<ComboboxSelected>>", lambda e: self.preview_file())
        self.file_box.bind("<Return>", lambda e: self.preview_file())
        self._btn(f, "열기…", "Soft", self.choose_file).grid(row=0, column=2)
        ttk.Label(f, text="오프셋", style="Muted.TLabel").grid(row=0, column=3, padx=(12, 4))
        self.offset_var = tk.StringVar(value="0x0000")
        e = ttk.Entry(f, textvariable=self.offset_var, width=7)
        e.grid(row=0, column=4)
        e.bind("<Return>", lambda ev: self.preview_file())
        self.verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="쓴 뒤 검증", variable=self.verify_var).grid(row=0, column=5, padx=(12, 4))
        self._btn(f, "⬆  파일 굽기", "write", self.start_write_file, chip=True).grid(row=0, column=6, padx=(8, 6))
        self._btn(f, "비교만", "Soft", self.start_compare_file, chip=True).grid(row=0, column=7, padx=2)
        self._tip_icon(f, "⚠ 쓰기는 EEPROM의 해당 주소 내용을 덮어씁니다.\n파일을 고르면 아래 표에 미리 보이고, [파일 굽기]를 눌러야 실제로 씁니다.\n"
                          "지원 형식: .hex (Logisim) / .ihx (Intel HEX) / .bin\n오프셋: 파일 안 주소에 더할 값 (예: 0x0100)", 0, 8)

    # ---- 편집
    def _panel_edit(self):
        card, f = self._card(self.panel_host, MODES["edit"]["color"])
        self.panels["edit"] = card
        card.grid(row=0, column=0, sticky="nsew")
        f.columnconfigure(10, weight=1)
        self.dirty_var = tk.StringVar(value="미기록 변경 0바이트")
        tk.Label(f, textvariable=self.dirty_var, bg=PAL["dirty"], fg=PAL["dirty_fg"], font=self.fonts["bold"], padx=10, pady=3
                 ).grid(row=0, column=0, sticky="w")
        self._btn(f, "변경분 쓰기", "edit", self.start_write_dirty, chip=True).grid(row=0, column=1, padx=(10, 4))
        self._btn(f, "모두 되돌리기", "Soft", self.revert_all).grid(row=0, column=2, padx=2)
        self._btn(f, "표 저장…", "Soft", self.save_buffer).grid(row=0, column=3, padx=2)
        ttk.Label(f, text="범위 채우기", style="Card.TLabel").grid(row=0, column=4, padx=(18, 4))
        self.fill_lo, self.fill_hi, self.fill_val = tk.StringVar(value="0x0000"), tk.StringVar(value="0x001F"), tk.StringVar(value="FF")
        for col, (lab, var, w) in enumerate((("시작", self.fill_lo, 7), ("끝", self.fill_hi, 7), ("값", self.fill_val, 4))):
            ttk.Label(f, text=lab, style="Muted.TLabel").grid(row=0, column=5 + col * 2, padx=(8, 3))
        ttk.Entry(f, textvariable=self.fill_lo, width=7).grid(row=0, column=6)
        ttk.Entry(f, textvariable=self.fill_hi, width=7).grid(row=0, column=8)
        ttk.Entry(f, textvariable=self.fill_val, width=4).grid(row=0, column=10, sticky="w")
        self._btn(f, "채우기", "Soft", self.fill_range).grid(row=0, column=11, padx=(8, 0))
        f.columnconfigure(12, weight=1)
        self._tip_icon(f, "표에서 바이트를 클릭하고 16진수 두 자리를 입력하세요. 오른쪽 ASCII 칸을 클릭하면 문자로 입력됩니다 (F2로 전환).\n"
                          "드래그/Shift+클릭으로 범위 선택(자동 복사), Ctrl+C 복사, Ctrl+V 붙여넣기, Ctrl+Z 되돌리기.\n"
                          "노란색 = EEPROM에 아직 안 쓴 변경. [변경분 쓰기]는 노란 칸만 쓰고 다시 읽어 검증합니다.", 0, 12)

    # ---- 도구
    def _panel_tools(self):
        card, f = self._card(self.panel_host, MODES["tools"]["color"])
        self.panels["tools"] = card
        card.grid(row=0, column=0, sticky="nsew")
        f.columnconfigure(4, weight=1)
        self._btn(f, "통신 확인", "tools", self.tool_ping, chip=True).grid(row=0, column=0, padx=(0, 6))
        self._btn(f, "빈 칸 검사 (8KB 읽기)", "tools", self.tool_blank, chip=True).grid(row=0, column=1, padx=6)
        self._btn(f, "표 내용 체크섬", "tools", self.tool_checksum).grid(row=0, column=2, padx=6)
        self._btn(f, "전체 지우기 (0xFF)", "danger", self.start_erase, chip=True).grid(row=0, column=3, padx=(24, 0))
        self._tip_icon(f, "점검과 정리 도구입니다. '전체 지우기'만 EEPROM 내용을 바꿉니다.\n"
                          "빈 칸 검사: 8KB 전체를 읽어서 0xFF가 아닌 바이트를 찾습니다.", 0, 4)

    # ---- 선택한 바이트 (오른쪽)
    def _inspector(self, work):
        card, f = self._card(work)
        card.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        f.columnconfigure(0, weight=1)
        tk.Label(f, text="선택한 바이트", font=self.fonts["bold"], bg=PAL["card"], fg=PAL["muted"]).grid(row=0, column=0, sticky="w")
        self.i_addr = tk.Label(f, text="—", font=self.fonts["monobig"], bg=PAL["card"], fg=MODES["edit"]["dark"])
        self.i_addr.grid(row=1, column=0, sticky="w", pady=(6, 0))
        row = ttk.Frame(f, style="Card.TFrame")
        row.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(row, text="값 (16진)", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.i_val = tk.StringVar()
        self.i_entry = ttk.Entry(row, textvariable=self.i_val, width=5, font=self.fonts["mono"])
        self.i_entry.pack(side="left")
        self.i_entry.bind("<Return>", lambda e: self.apply_inspector())
        ttk.Button(row, text="적용", style="Soft.TButton", command=self.apply_inspector).pack(side="left", padx=6)
        ttk.Label(f, text="비트 (클릭해서 켜고 끄기)", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 2))
        bits = ttk.Frame(f, style="Card.TFrame")
        bits.grid(row=4, column=0, sticky="w")
        self.bit_lbls = []
        for k in range(7, -1, -1):
            col = 7 - k
            tk.Label(bits, text=f"D{k}", font=self.fonts["small"], bg=PAL["card"], fg=PAL["muted"]).grid(row=0, column=col, padx=2)
            lb = tk.Label(bits, text="·", width=3, font=self.fonts["bold"], bg=PAL["unk_bg"], fg=PAL["muted"], cursor="hand2", pady=4)
            lb.grid(row=1, column=col, padx=2, pady=2)
            lb.bind("<Button-1>", lambda e, kk=k: self.toggle_bit(kk))
            self.bit_lbls.append((k, lb))
        self.i_info = tk.Label(f, text="", font=self.fonts["ui"], bg=PAL["card"], fg=PAL["text"], justify="left", anchor="w")
        self.i_info.grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.i_state = tk.Label(f, text="", font=self.fonts["bold"], bg=PAL["card"], fg=PAL["muted"], justify="left", anchor="w", wraplength=230)
        self.i_state.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self._btn(f, "이 바이트 EEPROM에서 읽기", "Soft", self.read_one, chip=True).grid(row=7, column=0, sticky="ew", pady=(14, 4))
        self._btn(f, "이 바이트 EEPROM에 쓰기", "Soft", self.write_one, chip=True).grid(row=8, column=0, sticky="ew")

    # ---------------------------------------------------------- 모드 / 상태 띠
    def show_mode(self, key):
        self.mode = key
        for k, b in self.mode_btns.items():
            b.set_active(k == key)
        self.panels[key].tkraise()
        if not self.busy and self.kind in ("idle", "ok", "fail"):
            pass

    def set_banner(self, kind, title, detail="", right=""):
        self.kind = kind
        self._banner_args = (kind, title, detail, right)
        bg, accent = BANNER[kind]
        self.banner.configure(bg=bg, highlightbackground=accent)
        for w in (self.b_icon, self.b_title, self.b_detail, self.b_right):
            w.configure(bg=bg, fg=accent if w is not self.b_detail else PAL["text"])
        self.b_title.configure(text=title)
        self.b_detail.configure(text=detail)
        self.b_right.configure(text=right)
        icon = {"idle": "●", "ok": "✔", "fail": "✖"}.get(kind, SPINNER[self.spin_i % 4])
        self.b_icon.configure(text=icon)
        self.progress.configure(style=f"{kind}.Horizontal.TProgressbar")
        if kind in ("idle", "ok", "fail"):
            self.progress.configure(value=0 if kind == "idle" else 100)
        if self.busy and self.cancelable:
            self.cancel_btn.grid()
        else:
            self.cancel_btn.grid_remove()

    def _tick(self):
        if self.busy:
            self.spin_i += 1
            self.b_icon.configure(text=SPINNER[self.spin_i % 4])
        self.after(120, self._tick)

    def set_busy(self, busy):
        self.busy = busy
        self.hv.locked = busy
        self.refresh_enabled()

    def refresh_enabled(self):
        st = "normal" if self.link.connected and not self.busy else "disabled"
        for menu, idx in self.menu_chip:
            menu.entryconfigure(idx, state=st)
        for b in self.chip_btns:
            b.configure(state="normal" if self.link.connected and not self.busy else "disabled")
        for b in self.local_btns:
            b.configure(state="disabled" if self.busy else "normal")
        self.conn_btn.configure(state="disabled" if self.busy else "normal")

    # ---------------------------------------------------------- 메시지 처리 (작업 스레드 -> 화면)
    def post(self, *msg):
        self.q.put(msg)

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "log":
                    self.log_line(m[1])
                elif kind == "phase":
                    self.phase_t0 = time.monotonic()
                    self.progress.configure(value=0)
                    self.set_banner(m[1], m[2], "", "")
                    self.title(f"{APP_NAME} — {m[2]}")
                elif kind == "progress":
                    cur, tot = m[1], max(m[2], 1)
                    self.progress.configure(value=100 * cur / tot)
                    el = time.monotonic() - self.phase_t0
                    eta = f"  ·  남은 시간 약 {int(el / cur * (tot - cur)) + 1}초" if cur and el > 1.5 and cur < tot else ""
                    self.b_right.configure(text=f"{100 * cur // tot}%{eta}")
                elif kind == "apply":
                    m[1]()
                elif kind == "done":
                    self._finish(m[1], m[2])
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def _finish(self, ok, msg):
        self.set_busy(False)
        self.title(APP_NAME)
        self.last_result = (ok, msg)
        if ok is None:                      # 결과 팝업 없이 정보만 (읽기 등)
            self.set_banner("ok", "완료", msg)
        elif ok:
            self.set_banner("ok", "완료", msg)
        else:
            self.set_banner("fail", "문제가 있습니다", msg)
            self.notify("문제가 있습니다", msg, error=True)
        self.cancel_btn.grid_remove()

    def notify(self, title, msg, error=False):
        if self.quiet:
            self.log_line(f"[{title}] {msg}")
        else:
            (messagebox.showerror if error else messagebox.showinfo)(title, msg)

    def log_line(self, s):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", s + "\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 3000:      # 8KB를 여러 번 읽어도 로그가 끝없이 커지지 않게
            self.log_box.delete("1.0", "1000.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _run(self, kind, title, fn, cancellable=True):
        """fn(ctx) 를 작업 스레드에서 실행. fn 은 (성공 여부 또는 None, 메시지)를 돌려준다."""
        if self.busy:
            return
        if not self.link.connected:
            self.notify("연결 필요", "먼저 포트를 고르고 [연결]을 눌러 주세요.", error=True)
            return
        self.cancel_evt.clear()
        self.cancelable = cancellable
        self.phase_t0 = time.monotonic()
        self.set_busy(True)
        self.set_banner(kind, title, "", "")
        self.title(f"{APP_NAME} — {title}")
        ctx = Ctx(self)

        def worker():
            try:
                ok, msg = fn(ctx)
            except Cancelled as e:
                ok, msg = False, str(e)
            except (DeviceError, FormatError, serial.SerialException, OSError) as e:
                ok, msg = False, str(e)
            except Exception as e:
                ok, msg = False, f"예상하지 못한 오류: {type(e).__name__}: {e}"
            self.post("done", ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    # ---------------------------------------------------------- 연결
    def refresh_ports(self):
        found = []
        for p in serial.tools.list_ports.comports():
            likely = (p.vid in (0x2341, 0x1B4F, 0x2A03, 0x1209)) or any(
                k in (p.description or "") for k in ("Arduino", "Leonardo", "Pro Micro", "SparkFun"))
            found.append((not likely, p.device, f"{p.device}  ·  {p.description}"))
        found.sort()
        values = [t for _, _, t in found]
        self.port_box["values"] = values
        last = self.settings.get("port")
        cur = self.port_var.get()
        if cur and not cur.startswith("COM"):     # socket:// 같은 직접 입력은 그대로 둔다
            return
        pick = next((t for _, d, t in found if d == last), values[0] if values else "")
        self.port_var.set(pick)

    def toggle_connect(self):
        if self.link.connected:
            self.link.close()
            self._conn_ui(False)
            return
        port = self.port_var.get().split("  ·  ")[0].strip()
        if not port:
            self.notify("포트 없음", "연결할 포트를 고르세요. USB를 꽂았는데 안 보이면 [새로고침]을 눌러 보세요.", error=True)
            return
        try:
            self.link.open(port)
        except (serial.SerialException, OSError, ValueError) as e:
            busy = "PermissionError" in str(e) or "액세스" in str(e) or "Access" in str(e)
            hint = ("\n\n다른 프로그램이 이 포트를 쓰고 있을 가능성이 큽니다. Arduino IDE의 시리얼 모니터/플로터, "
                    "다른 터미널 프로그램, 이미 열려 있는 이 프로그램을 닫고 다시 시도하세요.") if busy else ""
            self.notify("연결 실패", f"{port} 를 열지 못했습니다.\n\n{e}{hint}", error=True)
            return
        self.settings["port"] = port
        self._save_settings()
        self._conn_ui(True, port)

    def _conn_ui(self, on, port=""):
        self.conn_port = port
        bg, fg = BANNER["ok"] if on else BANNER["fail"]
        self.pill.configure(text=f"●  연결됨  {port}" if on else "●  연결 안 됨", bg=bg, fg=fg)
        self.conn_btn.configure(text="연결 해제" if on else "연결")
        menu, idx = self.menu_conn
        menu.entryconfigure(idx, label="연결 해제" if on else "연결")
        self.refresh_enabled()

    # ---------------------------------------------------------- 데이터 보기 쪽 콜백
    def _notice(self, title, detail=""):
        """복사/붙여넣기 같은 가벼운 알림을 상태 띠에 보여 준다 (작업 중에는 건드리지 않음)."""
        if not self.busy:
            self.set_banner("ok", title, detail)

    def _on_change(self):
        self.range_var.set(f"표시 범위 0x{self.hv.vlo:04X} ~ 0x{self.hv.vhi:04X}")
        n = len(self.hv.dirty_addrs())
        self.dirty_var.set(f"미기록 변경 {n}바이트")

    def _on_select(self, a):
        v = self.hv.buf.get(a)
        self.i_addr.configure(text=f"0x{a:04X}")
        self.i_val.set("" if v is None else f"{v:02X}")
        for k, lb in self.bit_lbls:
            if v is None:
                lb.configure(text="·", bg=PAL["unk_bg"], fg=PAL["muted"])
            else:
                on = v >> k & 1
                lb.configure(text=str(on), bg=MODES["edit"]["color"] if on else PAL["unk_bg"], fg=PAL["text"] if on else PAL["muted"])
        if v is None:
            self.i_info.configure(text="값을 아직 모릅니다.\n읽거나 입력하면 채워집니다.")
        else:
            ch = chr(v) if 32 <= v < 127 else "(표시 불가)"
            self.i_info.configure(text=f"10진수  {v}\n2진수   {v:08b}\n문자     {ch}")
        r = self.hv.sel_range()
        extra = f"\n\n선택 {r[1] - r[0] + 1}바이트  0x{r[0]:04X}~0x{r[1]:04X}" if r and r[0] != r[1] else ""
        area = "16진수" if self.hv.area == "hex" else "문자(ASCII)"
        self.i_info.configure(text=self.i_info.cget("text") + extra + f"\n\n입력 칸: {area}  (F2로 전환)")
        if a in self.hv.bad:
            exp, act = self.hv.bad[a]
            self.i_state.configure(text=f"검증 불일치\n기대 0x{exp:02X} / 실제 " + ("없음" if act is None else f"0x{act:02X}"), fg=PAL["bad_fg"])
        elif self.hv.is_dirty(a):
            self.i_state.configure(text="EEPROM에 아직 안 쓴 변경", fg=PAL["warn"])
        elif v is not None and a in self.hv.synced:
            self.i_state.configure(text="EEPROM과 같음", fg=PAL["good"])
        else:
            self.i_state.configure(text="", fg=PAL["muted"])

    def _row_changed(self, _e=None):
        v = self.row_var.get()
        self.settings["row_mode"] = v
        self._save_settings()
        self.hv.set_row_mode("auto" if v == "자동" else v)

    def goto(self):
        try:
            a = hexnum(self.goto_var.get(), "주소")
            if not 0 <= a < EEPROM_SIZE:
                raise ValueError(f"주소는 0x0000~0x{EEPROM_SIZE - 1:04X} 범위여야 합니다.")
        except ValueError as e:
            self.notify("주소 확인", str(e), error=True)
            return
        self.hv.select(a)
        self.hv.text.focus_set()

    def apply_inspector(self):
        a = self.hv.sel
        if a is None or self.busy:
            return
        try:
            v = hexnum(self.i_val.get(), "값")
            if not 0 <= v <= 0xFF:
                raise ValueError("값은 00~FF 범위여야 합니다.")
        except ValueError as e:
            self.notify("값 확인", str(e), error=True)
            return
        self.hv.set_byte(a, v)

    def toggle_bit(self, k):
        a = self.hv.sel
        if a is None or self.busy:
            return
        v = self.hv.buf.get(a, 0)
        self.hv.set_byte(a, v ^ (1 << k))

    def fill_range(self):
        if self.busy:
            return
        try:
            lo, hi, v = hexnum(self.fill_lo.get(), "시작"), hexnum(self.fill_hi.get(), "끝"), hexnum(self.fill_val.get(), "값")
            if not (0 <= lo <= hi < EEPROM_SIZE and 0 <= v <= 0xFF):
                raise ValueError("범위는 0x0000 ≤ 시작 ≤ 끝 ≤ 0x1FFF, 값은 00~FF 여야 합니다.")
        except ValueError as e:
            self.notify("입력 확인", str(e), error=True)
            return
        self.hv.set_bytes(lo, bytes([v]) * (hi - lo + 1))          # 한 번의 Ctrl+Z로 전부 되돌아감
        self._title("표 편집 (범위 채우기 후)", "edit")

    def revert_all(self):
        if self.busy:
            return
        if self.hv.dirty_addrs() and not self.quiet and not messagebox.askyesno("되돌리기", "아직 EEPROM에 쓰지 않은 변경을 모두 버릴까요?"):
            return
        self.hv.revert_all()

    def _title(self, text, mode=None, error=False):
        """데이터 보기 위쪽의 한 줄 설명. 내용이 바뀌는 일이 생기면 '데이터 보기' 탭으로 자동 전환한다."""
        self._view_state = (text, mode, error)
        c = PAL["err"] if error else (MODES[mode]["dark"] if mode else PAL["text"])
        self.view_title.configure(text=text, fg=c)
        self.nb.select(self.data_tab)

    # ---------------------------------------------------------- 파일
    def choose_file(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(
            initialdir=os.path.dirname(self.file_var.get()) if self.file_var.get() else (DEFAULT_DIR if os.path.isdir(DEFAULT_DIR) else HERE),
            title="EEPROM 이미지 선택", filetypes=[("EEPROM 이미지", "*.hex *.ihx *.bin"), ("모든 파일", "*.*")])
        if path:
            self.file_var.set(path)
            self.show_mode("write")
            self.preview_file()

    def preview_file(self):
        """파일을 읽어서 표에 '아직 안 쓴 값'으로 보여 준다 (EEPROM에는 아무것도 쓰지 않음)."""
        path = self.file_var.get().strip()
        if not path:
            return False
        try:
            image, fmt = load_image(path)
            if not image:
                raise FormatError("파일에 데이터가 없습니다.")
            off = hexnum(self.offset_var.get(), "시작 주소(오프셋)")
            applied = {a + off: v for a, v in image.items()}
            if min(applied) < 0 or max(applied) >= EEPROM_SIZE:
                raise FormatError(f"주소 0x{min(applied):04X}~0x{max(applied):04X} 가 EEPROM 범위(0x0000~0x{EEPROM_SIZE - 1:04X})를 벗어납니다.")
        except (FormatError, OSError, ValueError) as e:
            self.file_raw = None
            self._title(f"파일을 열 수 없습니다: {e}", error=True)
            return False
        if self.hv.undo and not self.quiet and not messagebox.askyesno("파일 열기", "표에서 편집한 내용이 사라집니다. 계속할까요?"):
            return False
        self.file_raw, self.file_name = image, os.path.basename(path)
        self._remember(path)
        self.hv.load(applied, bad={})
        self._title(f"파일 미리보기: {self.file_name} · {fmt} · {len(image)}바이트 · 0x{min(applied):04X}~0x{max(applied):04X}"
                    "   (EEPROM에는 아직 쓰지 않음)", "write")
        return True

    def _remember(self, path):
        rec = [p for p in self.settings.get("recent", []) if p != path]
        self.settings["recent"] = ([path] + rec)[:8]
        self.file_box["values"] = self.settings["recent"]
        self._save_settings()
        self._rebuild_recent()

    def open_recent(self, path):
        if self.busy:
            return
        self.file_var.set(path)
        self.show_mode("write")
        self.preview_file()

    def clear_recent(self):
        self.settings["recent"] = []
        self.file_box["values"] = []
        self._save_settings()
        self._rebuild_recent()

    def _rebuild_recent(self):
        m = self.recent_menu
        m.delete(0, "end")
        rec = self.settings.get("recent", [])
        if not rec:
            m.add_command(label="(최근 파일 없음)", state="disabled")
        for p in rec:
            m.add_command(label=p, command=lambda p=p: self.open_recent(p))
        m.add_separator()
        m.add_command(label="목록 지우기", command=self.clear_recent, state="normal" if rec else "disabled")

    # ---------------------------------------------------------- 메뉴 막대
    def _menu(self):
        bar = tk.Menu(self)
        self.menu_chip = []                      # 장치에 연결돼 있어야 누를 수 있는 항목들

        def add(menu, label, cmd, acc=None, chip=False):
            menu.add_command(label=label, command=cmd, accelerator=acc)
            if chip:
                self.menu_chip.append((menu, menu.index("end")))

        fm = tk.Menu(bar, tearoff=0)
        add(fm, "파일 열기…", self.choose_file, "Ctrl+O")
        self.recent_menu = tk.Menu(fm, tearoff=0)
        fm.add_cascade(label="최근 파일", menu=self.recent_menu)
        fm.add_separator()
        add(fm, "표 내용 저장…", self.save_buffer, "Ctrl+S")
        fm.add_separator()
        add(fm, "종료", self._on_close, "Alt+F4")
        bar.add_cascade(label="파일", menu=fm)
        self._rebuild_recent()

        em = tk.Menu(bar, tearoff=0)
        add(em, "되돌리기", self.hv.undo_last, "Ctrl+Z")
        add(em, "변경 모두 되돌리기", self.revert_all)
        em.add_separator()
        add(em, "선택 범위 복사", lambda: self.hv.copy(), "Ctrl+C")
        add(em, "붙여넣기", lambda: self.hv.paste(), "Ctrl+V")
        add(em, "입력 칸 전환 (16진수 ↔ 문자)", lambda: self.hv._key(types.SimpleNamespace(state=0, keysym="F2", char="")), "F2")
        em.add_separator()
        add(em, "주소로 이동", lambda: self.goto_entry.focus_set(), "Ctrl+G")
        add(em, "범위 채우기…", lambda: (self.show_mode("edit"), self.fill_lo.set(f"0x{(self.hv.sel or 0):04X}")))
        bar.add_cascade(label="편집", menu=em)

        dm = tk.Menu(bar, tearoff=0)
        add(dm, "연결", self.toggle_connect)
        self.menu_conn = (dm, dm.index("end"))
        add(dm, "포트 새로고침", self.refresh_ports)
        dm.add_separator()
        add(dm, "EEPROM 읽기", self.start_read, "F5", chip=True)
        add(dm, "파일 굽기", self.start_write_file, chip=True)
        add(dm, "파일과 비교", self.start_compare_file, chip=True)
        add(dm, "변경분 EEPROM에 쓰기", self.start_write_dirty, chip=True)
        dm.add_separator()
        add(dm, "통신 확인", self.tool_ping, chip=True)
        add(dm, "빈 칸 검사", self.tool_blank, chip=True)
        add(dm, "전체 지우기…", self.start_erase, chip=True)
        bar.add_cascade(label="장치", menu=dm)

        vm = tk.Menu(bar, tearoff=0)
        for i, (key, spec) in enumerate(MODES.items(), 1):
            add(vm, f"{spec['label']} 모드", lambda k=key: self.show_mode(k), f"Ctrl+{i}")
        vm.add_separator()
        add(vm, "데이터 보기", lambda: self.nb.select(self.data_tab))
        add(vm, "통신 로그", lambda: self.nb.select(self.log_tab))
        vm.add_separator()
        tm = tk.Menu(vm, tearoff=0)
        for label, val in (("라이트", "light"), ("다크", "dark"), ("시스템 설정 따르기", "system")):
            tm.add_radiobutton(label=label, variable=self.theme_var, value=val, command=self.change_theme)
        vm.add_cascade(label="테마", menu=tm)
        rm = tk.Menu(vm, tearoff=0)
        for val in ROW_CHOICES:
            rm.add_radiobutton(label=val if val == "자동" else f"{val}바이트", variable=self.row_var, value=val, command=self._row_changed)
        vm.add_cascade(label="표 한 줄 바이트 수", menu=rm)
        bar.add_cascade(label="보기", menu=vm)

        hm = tk.Menu(bar, tearoff=0)
        add(hm, "사용 방법", self.show_help)
        add(hm, f"{APP_NAME} 정보", self.show_about)
        bar.add_cascade(label="도움말", menu=hm)
        self.config(menu=bar)

    # ---------------------------------------------------------- 테마 전환
    def change_theme(self):
        if self.busy:
            self.notify("작업 중", "작업이 끝난 뒤에 테마를 바꿀 수 있습니다.")
            self.theme_var.set(self.settings.get("theme", "system"))
            return
        self.settings["theme"] = self.theme_var.get()
        self._save_settings()
        self.apply_theme()

    def apply_theme(self):
        set_theme(resolve_theme(self.theme_var.get()))
        self._rebuild_ui()
        set_titlebar_dark(self, THEME["dark"])

    def _rebuild_ui(self):
        """색을 바꾸려고 화면을 다시 만들되, 입력값/표 내용/로그/선택 모드는 그대로 옮긴다."""
        vals = {k: v.get() for k, v in vars(self).items() if isinstance(v, tk.Variable)}
        old = self.hv
        snap = (dict(old.buf), dict(old.synced), dict(old.bad), list(old.undo), old.sel, old.pin_lo, old.pin_hi, old.area, old.anchor)
        log = self.log_box.get("1.0", "end-1c")
        tab = self.nb.index(self.nb.select())
        mode, banner, view = self.mode, self._banner_args, self._view_state
        for w in self.winfo_children():
            w.destroy()
        self.chip_btns, self.local_btns, self.mode_btns, self.panels, self.menu_chip = [], [], {}, {}, []
        self.configure(bg=PAL["bg"])
        self._styles()
        self._build()
        self._menu()
        for k, v in vals.items():
            getattr(self, k).set(v)
        hv = self.hv
        hv.buf, hv.synced, hv.bad, hv.undo = snap[:4]
        hv.pin_lo, hv.pin_hi, hv.area = snap[5], snap[6], snap[7]
        hv.render()
        if snap[4] is not None:
            hv.select(snap[8] if snap[8] is not None else snap[4], see=False)
            if snap[8] is not None and snap[8] != snap[4]:
                hv.select(snap[4], see=False, extend=True)
        self._on_change()
        self.log_box.configure(state="normal")
        self.log_box.insert("1.0", log + ("\n" if log else ""))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        if view:
            self._title(*view)
        self.nb.select(tab)
        self.show_mode(mode)
        self._conn_ui(self.link.connected, self.conn_port)
        self.set_banner(*banner)

    def show_help(self):
        self.notify("사용 방법",
                    "1. 위 오른쪽에서 포트를 고르고 [연결]을 누릅니다.\n\n"
                    "2. 읽기 — EEPROM 내용을 표로 봅니다 (F5). EEPROM은 바뀌지 않습니다.\n\n"
                    "3. 쓰기 — 파일(.hex Logisim / .ihx Intel HEX / .bin)을 열고 [파일 굽기]. "
                    "쓴 뒤 자동으로 다시 읽어서 파일과 비교합니다.\n\n"
                    "4. 편집 — 표에서 바이트를 클릭하고 16진수 두 자리를 입력한 뒤 [변경분 EEPROM에 쓰기]. "
                    "노란색 칸이 아직 EEPROM에 안 쓴 값입니다.\n\n"
                    "5. 도구 — 통신 확인, 빈 칸 검사, 체크섬, 전체 지우기.\n\n"
                    "단축키: Ctrl+O 열기 · Ctrl+S 저장 · F5 읽기 · Ctrl+Z 되돌리기 · Ctrl+G 주소 이동 · Ctrl+1~4 모드 전환")

    def show_about(self):
        top = tk.Toplevel(self, bg=PAL["bg"])
        top.title(f"{APP_NAME} 정보")
        top.resizable(False, False)
        top.transient(self)
        try:
            top.iconbitmap(os.path.join(ASSETS, "romwriter.ico"))
        except tk.TclError:
            pass
        if self.logo:
            tk.Label(top, image=self.logo, bg=PAL["bg"]).pack(padx=40, pady=(24, 8))
        tk.Label(top, text=APP_NAME, font=self.fonts["title"], bg=PAL["bg"], fg=PAL["text"]).pack()
        tk.Label(top, text=f"버전 {APP_VERSION}", font=self.fonts["small"], bg=PAL["bg"], fg=PAL["muted"]).pack(pady=(2, 10))
        tk.Label(top, text="AT28C64 EEPROM 프로그래머용 PC 프로그램\nArduino Pro Micro 펌웨어(ROMWriter.ino v2.0)와 시리얼로 통신합니다.\n\nGyuPuter-4bit 프로젝트",
                 font=self.fonts["ui"], bg=PAL["bg"], fg=PAL["text"], justify="center").pack(padx=30)
        ttk.Button(top, text="닫기", style="read.TButton", command=top.destroy).pack(pady=18)
        top.grab_set()

    def _file_image(self):
        if not self.preview_file() or not self.file_raw:
            raise FormatError("먼저 올바른 파일을 고르세요.")
        off = hexnum(self.offset_var.get(), "시작 주소(오프셋)")
        return {a + off: v for a, v in self.file_raw.items()}

    def save_buffer(self):
        if not self.hv.buf:
            self.notify("저장할 데이터 없음", "표에 내용이 없습니다. 먼저 읽거나 파일을 여세요.", error=True)
            return
        path = filedialog.asksaveasfilename(title="표 내용 저장", defaultextension=".ihx",
                                            filetypes=[("Intel HEX", "*.ihx"), ("Logisim hex", "*.hex"), ("Binary", "*.bin")])
        if path:
            write_dump_file(path, self.hv.buf)
            self.log_line(f"저장: {path}")
            self.set_banner("ok", "저장했습니다", path)

    # ---------------------------------------------------------- 작업: 읽기
    def start_read(self):
        if self.busy:
            return
        try:
            lo, hi = hexnum(self.start_var.get(), "시작"), hexnum(self.end_var.get(), "끝")
            if not 0 <= lo <= hi < EEPROM_SIZE:
                raise ValueError(f"범위는 0x0000 ≤ 시작 ≤ 끝 ≤ 0x{EEPROM_SIZE - 1:04X} 여야 합니다.")
        except ValueError as e:
            self.notify("입력 확인", str(e), error=True)
            return
        if self.hv.dirty_addrs() and not self.quiet and not messagebox.askyesno(
                "읽기", "표에 EEPROM에 아직 쓰지 않은 변경이 있습니다. 읽으면 표 내용이 EEPROM 값으로 바뀝니다. 계속할까요?"):
            return
        self.show_mode("read")

        def task(ctx):
            data = self.link.dump(lo, hi, ctx.progress, self.cancel_evt)
            ctx.apply(lambda: (self.hv.load(data, synced=data, bad={}),
                               self._title(f"EEPROM에서 읽은 내용: 0x{lo:04X} ~ 0x{hi:04X}", "read")))
            return True, f"0x{lo:04X} ~ 0x{hi:04X}  ({len(data)}바이트)를 읽었습니다."

        self._run("read", "EEPROM에서 읽는 중", task)

    def read_one(self):
        a = self.hv.sel
        if a is None:
            return

        def task(ctx):
            v = self.link.read_byte(a)

            def done():
                self.hv.synced[a] = v
                self.hv.set_byte(a, v)
            ctx.apply(done)
            return True, f"0x{a:04X} = 0x{v:02X}  ({v})"

        self._run("read", "EEPROM에서 읽는 중", task, cancellable=False)

    # ---------------------------------------------------------- 작업: 쓰기 (공통 흐름)
    def _write_task(self, image, verify):
        def task(ctx):
            ctx.phase("write", "EEPROM에 쓰는 중")
            self.link.load(image_to_records(image), ctx.progress, self.cancel_evt)
            if not verify:
                return True, f"{len(image)}바이트를 보냈습니다. 검증하지 않았으므로 실제로 써졌는지는 알 수 없습니다."
            ctx.phase("verify", "검증하는 중 (다시 읽어서 비교)")
            actual = self.link.dump(min(image), max(image), ctx.progress, self.cancel_evt)
            bad, n = compare(image, actual)

            def apply():
                self.hv.synced.update(actual)
                self.hv.bad = {a: (e, g) for a, e, g in bad}
                self.hv.refresh()
                self.nb.select(self.data_tab)
            ctx.apply(apply)
            if not bad:
                return True, f"검증 성공: {n}바이트가 모두 일치합니다."
            lines = [f"0x{a:04X}: 기대 0x{e:02X} / 실제 " + ("없음" if g is None else f"0x{g:02X}") for a, e, g in bad[:6]]
            diff = 0
            for _, e, g in bad:
                if g is not None:
                    diff |= e ^ g
            hint = ""
            if diff:
                bits = ", ".join(f"DATA{i}" for i in range(8) if diff >> i & 1)
                hint = f"\n틀린 비트: {bits}  (항상 같은 비트만 틀리면 그 데이터 선의 배선이나 접촉을 의심해 보세요.)"
            return False, f"검증 실패: {n}바이트 중 {len(bad)}개가 다릅니다.\n" + "\n".join(lines) + hint
        return task

    def start_write_file(self):
        if self.busy:
            return
        try:
            image = self._file_image()
        except (FormatError, ValueError) as e:
            self.notify("입력 확인", str(e), error=True)
            return
        self.show_mode("write")
        self._run("write", "EEPROM에 쓰는 중", self._write_task(image, self.verify_var.get()))

    def start_write_dirty(self):
        if self.busy:
            return
        addrs = self.hv.dirty_addrs()
        if not addrs:
            self.notify("쓸 변경 없음", "표에서 바꾼 값이 없습니다. 바이트를 클릭해서 수정한 뒤 눌러 주세요.")
            return
        self.show_mode("edit")
        self._run("write", "EEPROM에 쓰는 중", self._write_task({a: self.hv.buf[a] for a in addrs}, True))

    def write_one(self):
        a = self.hv.sel
        if a is None or a not in self.hv.buf:
            self.notify("값 없음", "쓸 값이 없습니다. 값을 먼저 입력하세요.", error=True)
            return
        self._run("write", "EEPROM에 쓰는 중", self._write_task({a: self.hv.buf[a]}, True))

    def start_compare_file(self):
        if self.busy:
            return
        try:
            image = self._file_image()
        except (FormatError, ValueError) as e:
            self.notify("입력 확인", str(e), error=True)
            return
        self.show_mode("write")

        def task(ctx):
            actual = self.link.dump(min(image), max(image), ctx.progress, self.cancel_evt)
            bad, n = compare(image, actual)

            def apply():
                self.hv.load(actual, synced=actual, bad={a: (e, g) for a, e, g in bad})
                self._title(f"EEPROM에서 읽은 내용 (분홍색 = 파일과 다른 {len(bad)}바이트)", "write")
            ctx.apply(apply)
            if not bad:
                return True, f"{n}바이트가 모두 파일과 일치합니다."
            first = ", ".join(f"0x{a:04X}" for a, _, _ in bad[:6])
            return False, f"{n}바이트 중 {len(bad)}개가 파일과 다릅니다. (처음 몇 곳: {first})"

        self._run("verify", "비교하는 중 (EEPROM ↔ 파일)", task)

    # ---------------------------------------------------------- 작업: 도구
    def tool_ping(self):
        def task(ctx):
            ms = self.link.ping()
            return True, f"프로그래머가 응답합니다 (READ 왕복 {ms:.0f}ms)."
        self._run("verify", "통신 확인 중", task, cancellable=False)

    def tool_blank(self):
        def task(ctx):
            data = self.link.dump(0, EEPROM_SIZE - 1, ctx.progress, self.cancel_evt)
            used = [a for a, v in data.items() if v != 0xFF]
            ctx.apply(lambda: (self.hv.load(data, synced=data, bad={}), self._title("빈 칸 검사 결과: EEPROM 전체 8KB", "tools")))
            if not used:
                return True, "EEPROM 8KB가 모두 0xFF입니다. 비어 있습니다."
            return True, f"0xFF가 아닌 바이트가 {len(used)}개 있습니다 (처음 위치 0x{min(used):04X}, 마지막 0x{max(used):04X})."
        self._run("verify", "빈 칸 검사 중 (8KB 읽기)", task)

    def tool_checksum(self):
        if not self.hv.buf:
            self.notify("데이터 없음", "표에 내용이 없습니다.", error=True)
            return
        hi = max(self.hv.buf)
        raw = bytes(self.hv.buf.get(a, 0xFF) for a in range(hi + 1))
        self.set_banner("ok", "체크섬 (표 내용 0x0000 ~ 0x%04X)" % hi,
                        f"8비트 합계 0x{sum(raw) & 0xFF:02X}   ·   XOR 0x{self._xor(raw):02X}   ·   CRC32 0x{zlib.crc32(raw):08X}   ({len(raw)}바이트, 빈 칸은 0xFF로 계산)")

    @staticmethod
    def _xor(raw):
        x = 0
        for b in raw:
            x ^= b
        return x

    def start_erase(self):
        if self.busy:
            return
        if not self.quiet and not messagebox.askyesno(
                "전체 지우기", "EEPROM 8KB 전체를 0xFF로 덮어씁니다.\n시작하면 중간에 멈출 수 없고 수십 초 걸립니다. 계속할까요?"):
            return

        def task(ctx):
            self.link.erase(ctx.progress)
            data = {a: 0xFF for a in range(EEPROM_SIZE)}
            ctx.apply(lambda: (self.hv.load(data, synced=data, bad={}), self._title("전체 지우기 후 (모두 0xFF)", "tools")))
            return True, "전체 지우기가 끝났습니다. (지운 뒤 읽어서 확인한 것은 아닙니다. 필요하면 [빈 칸 검사]를 실행하세요.)"

        self._run("erase", "전체 지우는 중", task, cancellable=False)

    # ---------------------------------------------------------- 종료
    def _on_close(self):
        self.settings["geometry"] = self.geometry()
        self._save_settings()
        self.link.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
