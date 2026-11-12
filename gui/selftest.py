#!/usr/bin/env python3
"""실물 보드 없이 ROM Writer(통신 코드 + 화면)를 검증하는 자동 테스트.

ROMWriter.ino v2.0 의 시리얼 동작(READ, WRITE, HEXDUMP, LOAD, ERASE, parseValue 등)을
가짜 장치(TCP 소켓)로 흉내 내고, 그 장치에 프로그램을 연결해서 확인합니다.
(펌웨어를 읽고 만든 흉내라서, 실제 보드의 동작과 다를 수 있는 부분은 따로 확인해야 합니다.)

실행: python selftest.py
"""
import os
import socket
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import romwriter_core as rw
import romwriter_gui as gui
import tempfile

gui.SETTINGS = os.path.join(tempfile.gettempdir(), "romwriter_test_settings.json")   # 실제 설정을 건드리지 않도록


class FakeROMWriter(threading.Thread):
    """펌웨어 동작을 흉내 낸 가짜 장치. stuck_low_mask 로 '항상 0으로 읽히는 데이터 선'을 만들 수 있음."""

    def __init__(self, stuck_low_mask=0):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.mem = bytearray([0xFF]) * rw.EEPROM_SIZE
        self.stuck = stuck_low_mask
        self.loading = False
        self.start()

    @staticmethod
    def hexint(s):
        r = 0
        for c in s:
            r *= 16
            if c in "0123456789":
                r += int(c)
            elif c in "ABCDEF":
                r += ord(c) - 55
        return r

    def parse_value(self, s):
        s = s.strip().upper()
        if s.startswith("0X"):
            return self.hexint(s[2:])
        if any(c in "ABCDEF" for c in s):
            return self.hexint(s)
        return int(s) if s.lstrip("-").isdigit() else 0   # 숫자만 있으면 10진수 (GUI 가 항상 0x 를 붙이는 이유)

    def write(self, addr, data):
        self.mem[addr & 0x1FFF] = data & ~self.stuck & 0xFF

    def run(self):
        conn, _ = self.srv.accept()
        buf = b""
        out = lambda s: conn.sendall((s + "\r\n").encode("utf-8"))
        while True:
            try:
                chunk = conn.recv(256)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode().strip().upper()
                (self.handle_load_line if self.loading else self.handle_cmd)(line, out)

    def handle_load_line(self, line, out):
        if line == "END":
            self.loading = False
            out("HEX loading completed.")
            return
        if len(line) < 11 or line[0] != ":":
            out("Invalid HEX line format")
            return
        n, addr, rtype = self.hexint(line[1:3]), self.hexint(line[3:7]), self.hexint(line[7:9])
        if rtype == 1:
            self.loading = False
            out("End record received. Loading completed.")
            return
        if rtype != 0:
            return
        for i in range(n):
            self.write(addr + i, self.hexint(line[9 + 2 * i:11 + 2 * i]))
        out(f"Loaded {n} bytes at address 0x{addr:X}")       # 쓰기 성공 여부와 상관없이 항상 출력 (펌웨어와 동일)

    def handle_cmd(self, line, out):
        ci = line.find(",")
        cmd = line if ci < 0 else line[:ci]
        args = [] if ci < 0 else line[ci + 1:].split(",")
        if cmd == "READ":
            a = self.parse_value(args[0])
            d = self.mem[a]
            out(f"Address 0x{a:04X}: 0x{d:02X} (DEC: {d})")
        elif cmd == "WRITE":
            self.write(self.parse_value(args[0]), self.parse_value(args[1]))
        elif cmd == "HEXDUMP":
            lo, hi = self.parse_value(args[0]), self.parse_value(args[1])
            out("# Intel HEX Format")
            out(f"# Address range: 0x{lo:X} to 0x{hi:X}")
            for a in range(lo, hi + 1, 16):
                out(rw.make_record(a, bytes(self.mem[a:a + min(16, hi - a + 1)])))
            out(":00000001FF")
            out("# End of Intel HEX")
        elif cmd == "LOAD":
            self.loading = True                              # 펌웨어도 start 를 무시한다
            out("Ready to receive HEX data. Send ':' terminated lines.")
            out("Send 'END' when finished.")
        elif cmd == "ERASE":
            out("Starting EEPROM erase...")
            for p in range(100):
                out(f"Progress: {p}% ({p * 82}/8192 bytes)")
            self.mem[:] = bytes([0xFF]) * rw.EEPROM_SIZE
            out("✅ EEPROM erase completed successfully!")
        else:
            out("Unknown command. Available commands:")


FAILED = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def pump(app, timeout=40):
    end = time.time() + timeout
    time.sleep(0.05)
    while app.busy and time.time() < end:
        app.update()
        time.sleep(0.01)
    app.update()
    return not app.busy


def press(app, text):
    """16진 편집기에 키보드로 입력하는 것을 흉내 낸다."""
    for ch in text:
        app.hv._key(types.SimpleNamespace(state=0, keysym=ch, char=ch))


def connect(app, dev):
    app.link.close()
    app.port_var.set(f"socket://127.0.0.1:{dev.port}")
    app.toggle_connect()


# 테스트용 hex 파일(fib.hex, ctrl_rom1.hex 등)이 있는 폴더. 환경 변수 ROMWRITER_TEST_BUILD 로 바꿀 수 있음.
BUILD = os.environ.get("ROMWRITER_TEST_BUILD") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "GyuPuter-4bit", "build"))


def main():
    print("1) 파일 형식 해석")
    img, fmt = rw.load_image(os.path.join(BUILD, "fib.hex"))
    check("Logisim hex 해석", fmt == "Logisim hex" and [img[i] for i in range(7)] == [0x10, 0x40, 0x11, 0x70, 0x50, 0x20, 0x83])
    img2, fmt2 = rw.load_image(os.path.join(BUILD, "fib.ihx"))
    check("Intel HEX 해석 (.hex 와 같은 내용)", fmt2 == "Intel HEX" and img2 == img)
    img3, fmt3 = rw.load_image(os.path.join(BUILD, "fib.bin"))
    check("binary 해석", fmt3 == "binary" and img3 == img)
    cimg, _ = rw.load_image(os.path.join(BUILD, "ctrl_rom2.hex"))
    check("컨트롤 ROM 2 해석 (32바이트)", len(cimg) == 32 and cimg[3] == 0x60 and cimg[5] == 0xAC)
    try:
        rw.parse_ihex_line(":0100000010FF")
        check("체크섬 오류 감지", False)
    except rw.FormatError:
        check("체크섬 오류 감지", True)

    print("2) 화면 만들기, 연결")
    dev = FakeROMWriter()
    app = gui.App()
    app.quiet = True
    check("창이 오류 없이 만들어짐", app.winfo_exists() == 1)
    check("모드 버튼 4개 + 연결 전에는 장치 버튼이 꺼져 있음",
          len(app.mode_btns) == 4 and all(str(b.cget("state")) == "disabled" for b in app.chip_btns))
    connect(app, dev)
    check("연결", app.link.connected and "연결됨" in app.pill.cget("text"))
    check("연결하면 장치 버튼이 켜짐", all(str(b.cget("state")) == "normal" for b in app.chip_btns))

    print("3) 쓰기 모드: 파일 굽기 + 자동 검증")
    app.file_var.set(os.path.join(BUILD, "ctrl_rom1.hex"))
    app.offset_var.set("0x0000")
    check("파일 미리보기가 표에 표시되고 전부 '아직 안 쓴 변경'", app.preview_file() and len(app.hv.dirty_addrs()) == 32)
    app.start_write_file()
    check("쓰는 동안 상태 띠가 '쓰기' 색이고 취소 버튼이 보임",
          app.busy and app.kind in ("write", "verify"))
    pump(app)
    ok, msg = app.last_result
    check("굽기 + 검증 성공", ok is True and msg.startswith("검증 성공"), msg)
    check("끝나면 상태 띠가 완료", app.kind == "ok" and not app.busy)
    exp, _ = rw.load_image(os.path.join(BUILD, "ctrl_rom1.hex"))
    check("장치 메모리가 파일과 같음", all(dev.mem[a] == v for a, v in exp.items()))
    check("검증 후 '아직 안 쓴 변경'이 0", len(app.hv.dirty_addrs()) == 0)

    app.file_var.set(os.path.join(BUILD, "fib.hex"))
    app.offset_var.set("0x0100")
    app.start_write_file()
    pump(app)
    check("오프셋 0x0100 으로 굽기", app.last_result[0] is True and dev.mem[0x100] == 0x10 and dev.mem[0x106] == 0x83, str(app.last_result))

    labels = [app.recent_menu.entrycget(i, "label") for i in range(app.recent_menu.index("end") + 1) if app.recent_menu.type(i) == "command"]
    check("파일 메뉴의 '최근 파일'에 연 파일이 쌓임", any(l.endswith("fib.hex") for l in labels) and any(l.endswith("ctrl_rom1.hex") for l in labels), str(labels))
    app.open_recent(os.path.join(BUILD, "ctrl_rom1.hex"))
    check("최근 파일을 누르면 쓰기 모드로 열림", app.mode == "write" and app.file_name == "ctrl_rom1.hex" and len(app.hv.buf) == 32)
    app.offset_var.set("0x0000")
    check("메뉴 막대가 있음 (파일/편집/장치/보기/도움말)", app["menu"] != "" and app.nametowidget(app["menu"]).index("end") == 4)
    check("연결 중에는 장치 메뉴 항목이 켜짐", all(m.entrycget(i, "state") == "normal" for m, i in app.menu_chip))

    print("4) 읽기 모드")
    app.start_var.set("0x0100"); app.end_var.set("0x010F")
    app.start_read()
    pump(app)
    check("읽기 성공 + 표가 EEPROM 값으로 채워짐", app.last_result[0] is True and app.hv.buf.get(0x100) == 0x10 and len(app.hv.buf) == 16, str(app.last_result))
    app.preset_var.set("전체 8KB"); app._preset_changed()
    check("프리셋이 범위 칸을 채움", app.end_var.get() == "0x1FFF")

    app.nb.select(app.log_tab); app.start_read(); pump(app)
    check("읽기가 끝나면 '데이터 보기' 탭이 자동으로 앞에 옴", app.nb.select() == str(app.data_tab))
    print("5) 편집 모드: 클릭 대신 키보드로 수정, 되돌리기, 변경분 쓰기")
    app.hv.select(0x100)
    press(app, "A5")
    check("두 자리 입력으로 값이 바뀌고 다음 칸으로 이동", app.hv.buf[0x100] == 0xA5 and app.hv.sel == 0x101)
    check("바뀐 칸이 '미기록 변경'으로 집계됨", app.hv.dirty_addrs() == [0x100] and "1바이트" in app.dirty_var.get())
    press(app, "B")
    check("한 자리만 입력하면 값은 아직 그대로(입력 대기)", app.hv.buf[0x101] == 0x40 and app.hv.hi == 0xB)
    press(app, "7")
    check("두 번째 자리를 입력하면 확정", app.hv.buf[0x101] == 0xB7)
    app.hv.undo_last()
    check("Ctrl+Z 되돌리기", app.hv.buf[0x101] == 0x40)
    app.hv.set_byte(0x0105, 0xFE)
    app.start_write_dirty()
    pump(app)
    check("변경분만 EEPROM에 쓰고 검증", app.last_result[0] is True and dev.mem[0x100] == 0xA5 and dev.mem[0x105] == 0xFE, str(app.last_result))
    check("쓴 뒤 미기록 변경이 0", app.hv.dirty_addrs() == [])
    check("건드리지 않은 바이트는 그대로", dev.mem[0x101] == 0x40)

    app.hv.select(0x100)
    app.toggle_bit(0)
    check("비트 토글", app.hv.buf[0x100] == 0xA4)
    app.i_val.set("0x3c"); app.apply_inspector()
    check("인스펙터에서 16진수 입력(0x 접두사 허용)", app.hv.buf[0x100] == 0x3C)
    app.revert_all()
    check("변경 모두 되돌리기", app.hv.buf[0x100] == 0xA5 and app.hv.dirty_addrs() == [])

    app.fill_lo.set("0x0200"); app.fill_hi.set("0x020F"); app.fill_val.set("5A"); app.fill_range()
    check("범위 채우기", all(app.hv.buf[a] == 0x5A for a in range(0x200, 0x210)) and len(app.hv.dirty_addrs()) == 16)
    app.start_write_dirty()
    pump(app)
    check("채운 범위 쓰기", app.last_result[0] is True and dev.mem[0x20F] == 0x5A, str(app.last_result))

    app.hv.select(0x0300)
    press(app, "77")
    app.hv.select(0x0300)
    app.write_one()
    pump(app)
    check("선택한 바이트 하나만 쓰기", app.last_result[0] is True and dev.mem[0x300] == 0x77, str(app.last_result))
    dev.mem[0x301] = 0x42
    app.hv.select(0x301)
    app.read_one()
    pump(app)
    check("선택한 바이트 하나 읽기", app.hv.buf.get(0x301) == 0x42 and not app.hv.is_dirty(0x301), str(app.last_result))

    print("6) 도구 모드")
    app.tool_ping()
    pump(app)
    check("통신 확인", app.last_result[0] is True and "응답" in app.last_result[1], str(app.last_result))
    app.file_var.set(os.path.join(BUILD, "fib.hex")); app.offset_var.set("0x0100")
    app.start_compare_file()
    pump(app)
    check("파일과 비교 (fib.hex 를 0x0100 에 썼는데 0x100 을 편집해서 달라진 상태)", app.last_result[0] is False and 0x100 in app.hv.bad, str(app.last_result))
    app.tool_checksum()
    check("체크섬 표시", "CRC32" in app.b_detail.cget("text"))
    app.tool_blank()
    pump(app)
    check("빈 칸 검사가 쓴 곳을 찾아냄", app.last_result[0] is True and "0xFF가 아닌" in app.last_result[1], str(app.last_result))
    app.start_erase()
    check("지우는 동안에는 취소 버튼이 숨겨짐(중간에 멈출 수 없음)", app.busy and not app.cancel_btn.winfo_ismapped())
    pump(app, 60)
    check("전체 지우기", app.last_result[0] is True and set(dev.mem) == {0xFF}, str(app.last_result))
    check("지운 뒤 빈 칸 검사", (app.tool_blank(), pump(app), "비어 있습니다" in app.last_result[1])[2], str(app.last_result))

    print("7) 데이터 선 하나가 항상 0으로 읽히는 장치 (D5가 NC였을 때와 같은 증상)")
    dev2 = FakeROMWriter(stuck_low_mask=0b00001000)       # DATA3
    connect(app, dev2)
    app.file_var.set(os.path.join(BUILD, "fib.hex"))      # DATA3(0x08) 비트가 안 쓰이는 데이터
    app.offset_var.set("0x0000")
    app.start_write_file()
    pump(app)
    check("DATA3 이 안 쓰이는 데이터는 검증 통과", app.last_result[0] is True, str(app.last_result))
    app.file_var.set(os.path.join(BUILD, "ctrl_rom1.hex"))
    app.start_write_file()
    pump(app)
    ok, msg = app.last_result
    check("검증이 실패로 보고되고 상태 띠가 실패 색", ok is False and app.kind == "fail", msg)
    check("틀린 비트를 DATA3 으로만 짚어줌", "DATA3" in msg and "DATA0" not in msg and "DATA4" not in msg, msg)
    check("표에 분홍색 칸(검증 불일치)이 생기고 인스펙터가 기대/실제를 보여줌",
          len(app.hv.bad) > 0 and (app.hv.select(next(iter(app.hv.bad))), "기대" in app.i_state.cget("text"))[1])

    print("7-2) 다크모드 전환 (연결, 표 내용, 입력값, 모드를 유지해야 함)")
    dev3 = FakeROMWriter()                                    # 가짜 장치는 연결을 한 번만 받으므로 새로 만든다
    connect(app, dev3)
    app.theme_var.set("light"); app.change_theme()            # 이 PC의 시스템 테마와 상관없이 라이트에서 시작
    app.file_var.set(os.path.join(BUILD, "ctrl_rom1.hex")); app.offset_var.set("0x0000"); app.preview_file()
    app.show_mode("edit"); app.hv.select(0x05); press(app, "9C")
    before = (dict(app.hv.buf), dict(app.hv.undo and app.hv.synced), app.hv.sel, app.file_var.get(), app.mode)
    light_bg = gui.PAL["bg"]
    app.theme_var.set("dark"); app.change_theme()
    check("다크 팔레트가 적용됨", gui.THEME["dark"] and gui.PAL["bg"] != light_bg and gui.PAL["bg"] == "#1D1B29")
    check("전환 뒤에도 표 내용/선택/파일 경로/모드가 그대로", (dict(app.hv.buf), dict(app.hv.undo and app.hv.synced), app.hv.sel, app.file_var.get(), app.mode) == before)
    check("전환 뒤에도 편집 내용이 '미기록 변경'으로 남고 되돌리기도 됨", 0x05 in app.hv.dirty_addrs() and len(app.hv.undo) > 0)
    check("전환 뒤에도 연결 상태가 유지됨", app.link.connected and "연결됨" in app.pill.cget("text") and all(str(b.cget("state")) == "normal" for b in app.chip_btns))
    check("화면 색이 실제로 다크로 칠해짐", app.cget("bg") == "#1D1B29" and app.hv.text.cget("bg") == gui.PAL["card"] == "#282638")
    app.start_write_dirty(); pump(app)
    check("다크모드에서도 쓰기 동작", app.last_result[0] is True and dev3.mem[0x05] == 0x9C, str(app.last_result))
    app.hv.select(0x06); press(app, "11"); n = len(app.hv.undo); app.hv.undo_last()
    check("전환 뒤 새 표에서 편집과 되돌리기가 동작", app.hv.buf.get(0x06) != 0x11 and len(app.hv.undo) == n - 1)
    app.theme_var.set("light"); app.change_theme()
    check("라이트로 되돌리기", not gui.THEME["dark"] and gui.PAL["bg"] == light_bg and app.hv.buf.get(0x05) == 0x9C)
    app.theme_var.set("system"); app.change_theme()
    check("시스템 설정 따르기도 오류 없이 적용", gui.THEME["name"] in ("light", "dark") and app.settings.get("theme") == "system")
    app.busy = True; app.theme_var.set("dark"); app.change_theme(); app.busy = False
    check("작업 중에는 테마를 바꾸지 않음", gui.THEME["name"] == gui.resolve_theme("system") and app.theme_var.get() == "system")
    app.theme_var.set("dark"); app.change_theme()
    check("다크에서 모든 모드 패널이 만들어져 있음", set(app.panels) == {"read", "write", "edit", "tools"} and len(app.mode_btns) == 4)
    app.theme_var.set("light"); app.change_theme()

    print("7-3) 표 한 줄 바이트 수 가변 + 리본/왼쪽 정렬 배치")
    app.hv.load({a: (a * 7 + 3) & 0xFF for a in range(0, 0x400)}, synced={}, bad={})
    app.show_mode("edit"); app.update()
    probe = [0, 7, 8, 15, 16, 31, 33, 63, 64, 100, 0x3FF]       # 읽은 범위(0~0x3FF) 안의 주소들
    for n in (8, 16, 32, 64):
        app.row_var.set(str(n)); app._row_changed(); app.update()
        hv = app.hv
        ok = hv.n == n
        for a in probe:
            v = hv.buf.get(a)
            want = "--" if v is None else f"{v:02X}"
            ok &= hv.text.get(hv._idx(a), hv._idx(a) + "+2c") == want
            want_c = "." if v is None or not 32 <= v < 127 else chr(v)
            ok &= hv.text.get(hv._idx(a, True)) == want_c
        hdr = hv.text.get("1.0", "1.end")
        ok &= hdr.split()[1] == "00" and hdr.split()[n] == f"{n - 1:02X}"
        check(f"한 줄 {n}바이트: 헤더(00~{n - 1:02X})와 모든 칸의 위치가 맞음", ok, hdr[:40])
        hv.select(0x100)
        hv._key(types.SimpleNamespace(state=0, keysym="Down", char=""))
        hv._key(types.SimpleNamespace(state=0, keysym="Right", char=""))
        ok = hv.sel == 0x100 + n + 1
        hv._key(types.SimpleNamespace(state=0, keysym="Up", char=""))
        ok &= hv.sel == 0x100 + 1
        hv._key(types.SimpleNamespace(state=0, keysym="End", char=""))
        ok &= hv.sel == (0x100 // n) * n + n - 1
        hv._key(types.SimpleNamespace(state=0, keysym="Home", char=""))
        ok &= hv.sel == 0x100
        check(f"한 줄 {n}바이트: 방향키 이동(위/아래는 {n}칸), Home/End", ok, str(hex(hv.sel)))
        hv.text.see(hv._idx(0x1A5)); app.update()
        x, y, w, h = hv.text.bbox(hv._idx(0x1A5))
        hv._press(types.SimpleNamespace(x=x + 3, y=y + 3, state=0)); hv._release(None)
        check(f"한 줄 {n}바이트: 마우스 클릭으로 올바른 바이트가 선택됨", hv.sel == 0x1A5, hex(hv.sel or 0))
    app.row_var.set("16"); app._row_changed()
    app.hv.set_byte(0x123, 0xEE)
    app.row_var.set("32"); app._row_changed(); app.update()
    check("한 줄 수를 바꿔도 '아직 안 쓴 변경' 색이 새 위치에 유지됨", "dirty" in app.hv.text.tag_names(app.hv._idx(0x123)))
    app.theme_var.set("dark"); app.change_theme()
    check("테마를 바꿔도 한 줄 바이트 수 설정이 유지됨", app.hv.n == 32 and app.row_var.get() == "32" and app.settings.get("row_mode") == "32")
    app.theme_var.set("light"); app.change_theme()
    app.row_var.set("자동"); app._row_changed()
    app.geometry("1900x900"); app.update(); time.sleep(0.4); app.update(); app.hv._refit()
    wide = app.hv.n
    app.geometry("940x700"); app.update(); time.sleep(0.4); app.update(); app.hv._refit()
    narrow = app.hv.n
    check("자동: 창이 넓으면 많이, 좁으면 적게 보여 줌", wide >= 32 and narrow <= 16 and wide > narrow, f"wide={wide} narrow={narrow}")
    app.geometry("1120x780"); app.update(); time.sleep(0.3); app.update(); app.update_idletasks()   # 크기를 되돌린 뒤 배치가 끝나길 기다림
    mx = app.mode_btns["read"]; ph = app.panel_host
    check("리본: 모드 탭이 도구 줄 바로 위에 붙어 있음", 0 <= ph.winfo_rooty() - (mx.winfo_rooty() + mx.winfo_height()) <= 3,
          f"gap={ph.winfo_rooty() - (mx.winfo_rooty() + mx.winfo_height())}")
    check("리본: 탭들이 왼쪽에 모여 있음", app.mode_btns["tools"].winfo_rootx() + app.mode_btns["tools"].winfo_width() < app.winfo_rootx() + app.winfo_width() // 2)
    check("연결 줄(포트/새로고침/연결/상태)이 왼쪽 정렬", app.pill.winfo_rootx() < app.winfo_rootx() + app.winfo_width() * 0.5
          and app.port_box.winfo_rootx() - app.winfo_rootx() < 40, f"port x={app.port_box.winfo_rootx() - app.winfo_rootx()}")
    check("선택한 바이트 패널이 데이터 표 왼쪽에 있음", app.i_addr.winfo_rootx() < app.hv.winfo_rootx())
    check("연결 줄이 도구 줄 아래, 상태 띠 위에 있음", ph.winfo_rooty() < app.port_box.winfo_rooty() < app.banner.winfo_rooty() < app.hv.winfo_rooty())

    print("7-4) 표시 범위: 읽은 범위까지만, 주소 이동하면 넓어짐")
    DOWN = types.SimpleNamespace(state=0, keysym="Down", char="")
    app.show_mode("read"); app.row_var.set("16"); app._row_changed()
    app.start_var.set("0x0000"); app.end_var.set("0x00FF"); app.start_read(); pump(app)
    hv = app.hv
    lines = int(hv.text.index("end-1c").split(".")[0])
    check("0x00FF까지 읽으면 표도 0x00FF까지만 (16줄 + 헤더)", (hv.vlo, hv.vhi) == (0, 0xFF) and lines == 17, f"{hv.vlo:#x}~{hv.vhi:#x} lines={lines}")
    check("표시 범위 안내 라벨이 읽은 범위를 보여줌", "0x0000 ~ 0x00FF" in app.range_var.get(), app.range_var.get())
    app.goto_var.set("0x0800"); app.goto()
    check("주소 이동으로 표 밖(0x0800)에 가면 그 주소까지 넓어짐", hv.vhi >= 0x800 and hv.sel == 0x800 and
          hv.text.get(hv._idx(0x800), hv._idx(0x800) + "+2c") == "--", f"{hv.vlo:#x}~{hv.vhi:#x}")
    check("넓어진 범위가 안내 라벨에 반영됨", "0x080F" in app.range_var.get(), app.range_var.get())
    app.start_var.set("0x0000"); app.end_var.set("0x001F"); app.start_read(); pump(app)
    check("새로 읽으면 표시 범위가 데이터 크기로 다시 줄어듦", (hv.vlo, hv.vhi) == (0, 0x1F) and hv.pin_hi is None, f"{hv.vlo:#x}~{hv.vhi:#x}")
    app.start_var.set("0x0100"); app.end_var.set("0x01FF"); app.start_read(); pump(app)
    check("0x0100부터 읽으면 표도 0x0100부터 시작 (앞쪽 빈 줄 없음)", (hv.vlo, hv.vhi) == (0x100, 0x1FF), f"{hv.vlo:#x}~{hv.vhi:#x}")
    hv.select(0x1FF); hv._key(DOWN)
    check("마지막 줄에서 아래 방향키를 누르면 한 줄씩 늘어남", hv.vhi == 0x20F and hv.sel == 0x20F, f"{hv.vhi:#x} sel={hv.sel}")
    hv.select(0x400); press(app, "5A")
    check("표 밖으로 이동해서 값을 입력해도 편집됨", hv.buf.get(0x400) == 0x5A and hv.is_dirty(0x400) and hv.vhi >= 0x400)
    app.row_var.set("32"); app._row_changed()
    check("한 줄 바이트 수를 바꾸면 범위가 줄 단위로 다시 맞춰짐", hv.vlo % 32 == 0 and (hv.vhi + 1) % 32 == 0, f"{hv.vlo:#x}~{hv.vhi:#x}")
    hv.load({})
    check("데이터가 없으면 한 줄만 보임", hv.rows == 1)
    app.file_var.set(os.path.join(BUILD, "ctrl_rom1.hex")); app.offset_var.set("0x0000"); app.preview_file()
    check("32바이트 파일은 0x1F까지만 표시", (hv.vlo, hv.vhi) == (0, 0x1F), f"{hv.vlo:#x}~{hv.vhi:#x}")
    app.hv.select(0x1FFF)
    check("가장 끝 주소(0x1FFF)까지 이동할 수 있음", hv.vhi == 0x1FFF and hv.text.get(hv._idx(0x1FFF), hv._idx(0x1FFF) + "+2c") == "--")
    app.row_var.set("자동"); app._row_changed()

    print("7-5) ASCII 칸 입력, 드래그 선택(자동 복사), 붙여넣기")
    app.show_mode("edit"); app.row_var.set("16"); app._row_changed(); app.update()
    hv = app.hv
    hv.load({a: 0 for a in range(0, 0x100)}, synced={a: 0 for a in range(0, 0x100)})
    app.update()
    E = lambda x, y, s=0: types.SimpleNamespace(x=x, y=y, state=s)

    def cell_xy(a, asc=False):
        hv.text.see(hv._idx(a, asc)); app.update()
        x, y, w, h = hv.text.bbox(hv._idx(a, asc))
        return x + 2, y + 2

    def click(a, asc=False, shift=False):
        x, y = cell_xy(a, asc)
        hv._press(E(x, y, 1 if shift else 0)); hv._release(E(x, y))

    def drag(a0, a1, asc=False):
        x0, y0 = cell_xy(a0, asc)
        hv._press(E(x0, y0))
        x1, y1 = cell_xy(a1, asc)
        hv._drag(E(x1, y1)); hv._release(E(x1, y1))

    def clip():
        return app.clipboard_get()

    click(0x20); a1 = hv.area
    click(0x20, asc=True); a2 = hv.area
    click(0x20); a3 = hv.area
    check("클릭한 칸에 따라 입력 칸이 정해짐 (16진수 칸 / ASCII 칸)", (a1, a2, a3) == ("hex", "asc", "hex"), str((a1, a2, a3)))
    click(0x30, asc=True); press(app, "Hi!")
    check("ASCII 칸에서 글자를 치면 그 문자의 바이트가 들어가고 다음 칸으로 이동", bytes(hv.buf[0x30 + i] for i in range(3)) == b"Hi!" and hv.sel == 0x33)
    click(0x40); press(app, "4A")
    check("16진수 칸에서는 여전히 16진수로 입력됨", hv.buf[0x40] == 0x4A)
    click(0x50, asc=True); press(app, "가")
    check("한글 같은 문자는 UTF-8 여러 바이트로 들어감", bytes(hv.buf[0x50 + i] for i in range(3)) == "가".encode("utf-8") and hv.sel == 0x53)
    hv._key(types.SimpleNamespace(state=0, keysym="F2", char=""))
    check("F2로 16진수 칸 ↔ 문자 칸 전환", hv.area == "hex")
    hv._key(types.SimpleNamespace(state=0, keysym="F2", char=""))
    check("F2를 한 번 더 누르면 되돌아옴", hv.area == "asc")

    hv.set_bytes(0x60, bytes(range(0x41, 0x51)))                       # 'A'..'P'
    drag(0x60, 0x65)
    check("드래그하면 범위가 선택되고 16진수 형식으로 클립보드에 복사됨",
          hv.sel_range() == (0x60, 0x65) and clip() == "41 42 43 44 45 46", f"{hv.sel_range()} {clip()!r}")
    check("복사했다는 알림이 상태 띠에 표시됨", "복사" in app.b_title.cget("text") and "6바이트" in app.b_detail.cget("text"), app.b_detail.cget("text"))
    drag(0x60, 0x65, asc=True)
    check("ASCII 칸에서 드래그하면 글자로 복사됨", clip() == "ABCDEF", repr(clip()))
    click(0x70); click(0x73, shift=True)
    check("Shift+클릭으로 범위가 넓어짐", hv.sel_range() == (0x70, 0x73))
    hv._key(types.SimpleNamespace(state=1, keysym="Right", char=""))
    check("Shift+방향키로도 범위가 넓어짐", hv.sel_range() == (0x70, 0x74))
    app.update()
    check("선택한 바이트 패널에 선택 범위와 입력 칸이 표시됨", "선택 5바이트" in app.i_info.cget("text") and "입력 칸" in app.i_info.cget("text"), app.i_info.cget("text"))
    drag(0x60, 0x63)
    app.clipboard_clear(); hv.copy()
    check("Ctrl+C 복사 (16진수 칸에서 선택했을 때)", hv.area in ("hex", "asc") and clip() in ("41 42 43 44", "ABCD"))

    app.clipboard_clear(); app.clipboard_append("DE AD BE EF")
    click(0x80); n0 = len(hv.undo); ok = hv.paste()
    check("16진수 칸에서 'DE AD BE EF' 붙여넣기", ok and bytes(hv.buf[0x80 + i] for i in range(4)) == bytes.fromhex("DEADBEEF") and hv.sel_range() == (0x80, 0x83))
    check("붙여넣기 알림(16진수 해석)", "붙여넣었습니다" in app.b_title.cget("text") and "16진수" in app.b_detail.cget("text"), app.b_detail.cget("text"))
    check("붙여넣기는 한 번의 되돌리기로 전부 되돌아감", len(hv.undo) == n0 + 1)
    hv.undo_last()
    check("Ctrl+Z 한 번으로 붙여넣은 4바이트가 모두 원래대로", all(hv.buf[0x80 + i] == 0 for i in range(4)) and len(hv.undo) == n0)
    for text, want, label in (("0x10,0x20;30", bytes([0x10, 0x20, 0x30]), "쉼표/세미콜론/0x 접두사"),
                              ("ABCDEF", bytes.fromhex("ABCDEF"), "붙여 쓴 16진수 문자열"),
                              ("ABC", b"ABC", "홀수 길이는 16진수가 아니라 문자"),
                              ("Hello World", b"Hello World", "16진수가 아닌 글자가 섞이면 문자")):
        app.clipboard_clear(); app.clipboard_append(text)
        click(0x90); hv.paste()
        got = bytes(hv.buf[0x90 + i] for i in range(len(want)))
        check(f"붙여넣기 해석: {label} ({text!r})", got == want, repr(got))
    app.clipboard_clear(); app.clipboard_append("A53C")
    click(0xA0, asc=True); hv.paste()
    check("ASCII 칸에서는 'A53C'를 16진수가 아니라 문자로 붙여넣음", bytes(hv.buf[0xA0 + i] for i in range(4)) == b"A53C")
    app.clipboard_clear(); app.clipboard_append("가나")
    click(0xB0, asc=True); hv.paste()
    check("한글 붙여넣기는 UTF-8 바이트로", bytes(hv.buf[0xB0 + i] for i in range(6)) == "가나".encode("utf-8"))
    app.clipboard_clear(); app.clipboard_append("11 22 33 44")
    hv.area = "hex"; hv.select(0x1FFE); ok = hv.paste()
    check("끝 주소를 넘는 붙여넣기는 EEPROM 끝(0x1FFF)에서 자르고 알려줌",
          ok and hv.buf.get(0x1FFE) == 0x11 and hv.buf.get(0x1FFF) == 0x22 and 0x2000 not in hv.buf and "뺐습니다" in app.b_detail.cget("text"), app.b_detail.cget("text"))
    hv.select(0x10); app.clipboard_clear()
    check("클립보드가 비어 있으면 아무것도 바뀌지 않음", hv.paste() is False)
    hv.locked = True; app.clipboard_append("55"); before = dict(hv.buf)
    check("작업 중(잠김)에는 붙여넣기가 무시됨", hv.paste() is False and hv.buf == before)
    hv.locked = False
    check("parse_hex_text 해석", gui.parse_hex_text("A5 3C") == b"\xa5\x3c" and gui.parse_hex_text("a5,3c") == b"\xa5\x3c"
          and gui.parse_hex_text("0x7F") == b"\x7f" and gui.parse_hex_text("F") == b"\x0f" and gui.parse_hex_text("ABC") is None
          and gui.parse_hex_text("xyz") is None and gui.parse_hex_text("   ") is None and gui.parse_hex_text("12 3") == b"\x12\x03")
    n0 = len(hv.undo)
    app.fill_lo.set("0x00C0"); app.fill_hi.set("0x00CF"); app.fill_val.set("EE"); app.fill_range()
    check("범위 채우기도 한 번의 되돌리기로 묶임", len(hv.undo) == n0 + 1 and all(hv.buf[a] == 0xEE for a in range(0xC0, 0xD0)))
    hv.undo_last()
    check("범위 채우기를 Ctrl+Z 한 번으로 되돌림", all(hv.buf[a] == 0 for a in range(0xC0, 0xD0)))
    labels = [app.nametowidget(app["menu"]).entrycget(i, "label") for i in range(app.nametowidget(app["menu"]).index("end") + 1)
              if app.nametowidget(app["menu"]).type(i) == "cascade"]
    check("메뉴 막대 구성 유지", labels[:2] == ["파일", "편집"], str(labels))
    app.theme_var.set("dark"); app.change_theme()
    check("테마를 바꿔도 입력 칸과 선택 범위가 유지됨", app.hv.area == "hex" and app.hv.sel_range() is not None)
    app.theme_var.set("light"); app.change_theme()

    print("8) 기타")
    app.link.close(); app._conn_ui(False)
    check("연결을 끊으면 장치 버튼이 다시 꺼짐", all(str(b.cget("state")) == "disabled" for b in app.chip_btns))
    app.start_write_file()
    check("연결이 없으면 작업을 시작하지 않음", not app.busy)
    app.offset_var.set("0x1FFF")
    check("범위 밖 오프셋은 데이터 보기 제목에 오류로 표시", app.preview_file() is False and "벗어납니다" in app.view_title.cget("text"))
    app.destroy()

    print()
    print("결과:", "모두 통과" if not FAILED else f"실패 {len(FAILED)}개: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
