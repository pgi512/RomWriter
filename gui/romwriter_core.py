"""ROM Writer 의 화면과 무관한 부분: 파일 형식 해석, 시리얼 통신, 비교.

펌웨어(ROMWriter.ino v2.0)를 읽고 확인한 동작:
  * LOAD,start 의 start 값은 무시되고, 주소는 HEX 레코드 안의 값을 쓴다.
  * 쓰기에 실패해도 펌웨어는 "Loaded N bytes"라고 답한다 -> 굽고 나서 반드시 다시 읽어 비교해야 한다.
  * parseValue 는 숫자만 있으면 10진수로 해석 -> 주소는 항상 0x 접두사를 붙여 보낸다.
"""
import re
import time

import serial

EEPROM_SIZE = 0x2000          # AT28C64: 8KB
BAUD = 115200


# ============================================================ 파일 형식 처리
class FormatError(Exception):
    pass


def parse_ihex_line(line):
    """Intel HEX 한 줄 -> (type, addr, bytes). 체크섬 검사 포함."""
    line = line.strip()
    if not line.startswith(":") or len(line) < 11:
        raise FormatError(f"Intel HEX 형식이 아닙니다: {line[:30]!r}")
    try:
        raw = bytes.fromhex(line[1:])
    except ValueError:
        raise FormatError(f"16진수가 아닌 글자가 있습니다: {line[:30]!r}")
    if len(raw) != raw[0] + 5:
        raise FormatError(f"길이가 맞지 않습니다: {line[:30]!r}")
    if sum(raw) & 0xFF:
        raise FormatError(f"체크섬이 맞지 않습니다: {line[:30]!r}")
    return raw[3], (raw[1] << 8) | raw[2], raw[4:4 + raw[0]]


def parse_intel_hex(text):
    image, upper = {}, 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rtype, addr, data = parse_ihex_line(line)
        if rtype == 0x00:
            for i, b in enumerate(data):
                image[upper + addr + i] = b
        elif rtype == 0x01:
            break
        elif rtype == 0x04 and len(data) == 2:
            upper = ((data[0] << 8) | data[1]) << 16
        elif rtype == 0x02 and len(data) == 2:
            upper = ((data[0] << 8) | data[1]) << 4
    return image


def parse_logisim_hex(text):
    """Logisim 'v2.0 raw' / 'v3.0 hex words plain' 형식. 'n*값' 반복 표기를 지원."""
    values = []
    for line in text.splitlines()[1:]:
        line = line.split("#")[0]
        for tok in line.split():
            if "*" in tok:
                cnt, val = tok.split("*", 1)
                values += [int(val, 16)] * int(cnt)      # 반복 횟수는 10진수로 해석 (Logisim 문서로 확인하지 못함)
            else:
                values.append(int(tok, 16))
    if any(v > 0xFF for v in values):
        raise FormatError("8비트(0xFF)를 넘는 값이 있습니다. 데이터 폭이 8비트인 파일이 맞나요?")
    return {i: v for i, v in enumerate(values)}


def load_image(path):
    """파일 -> (dict 주소->바이트, 형식 이름). 내용을 보고 형식을 판별."""
    with open(path, "rb") as fh:
        raw = fh.read()
    head = raw[:16].lstrip()
    try:
        if head.startswith(b":"):
            return parse_intel_hex(raw.decode("ascii", "replace")), "Intel HEX"
        if head.startswith(b"v2.0") or head.startswith(b"v3.0"):
            return parse_logisim_hex(raw.decode("ascii", "replace")), "Logisim hex"
    except ValueError as e:
        raise FormatError(f"파일을 해석하지 못했습니다: {e}")
    if path.lower().endswith(".bin") or not raw[:64].strip().replace(b"\n", b"").isalnum():
        return {i: b for i, b in enumerate(raw)}, "binary"
    raise FormatError("알 수 없는 형식입니다 (.hex Logisim, .ihx Intel HEX, .bin 중 하나여야 합니다).")


def make_record(addr, data):
    rec = [len(data), addr >> 8, addr & 0xFF, 0x00] + list(data)
    return ":" + "".join(f"{b:02X}" for b in rec) + f"{(-sum(rec)) & 0xFF:02X}"


def image_to_records(image, per_line=16):
    """주소가 이어지는 구간별로 잘라서 Intel HEX 레코드 목록을 만든다."""
    recs, run, start = [], [], None
    for addr in sorted(image):
        if run and (addr != start + len(run) or len(run) == per_line):
            recs.append(make_record(start, run))
            run = []
        if not run:
            start = addr
        run.append(image[addr])
    if run:
        recs.append(make_record(start, run))
    return recs


def write_dump_file(path, image):
    """내용을 파일로 저장. 확장자로 형식을 고름 (.ihx / .hex(Logisim) / .bin)."""
    import os
    lo, hi = min(image), max(image)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bin":
        with open(path, "wb") as fh:
            fh.write(bytes(image.get(a, 0xFF) for a in range(lo, hi + 1)))
    elif ext == ".hex":      # Logisim 형식은 주소 0부터 시작하는 단순 목록
        flat0 = [image.get(a, 0xFF) for a in range(0, hi + 1)]
        with open(path, "w", newline="\n") as fh:
            fh.write("v3.0 hex words plain\n")
            for i in range(0, len(flat0), 8):
                fh.write(" ".join(f"{b:x}" for b in flat0[i:i + 8]) + "\n")
    else:
        with open(path, "w", newline="\n") as fh:
            fh.write("\n".join(image_to_records(image)) + "\n:00000001FF\n")


def compare(expected, actual):
    """expected 의 모든 바이트가 actual 과 같은지. -> (불일치 목록[(주소, 기대, 실제)], 비교한 개수)"""
    bad = [(a, v, actual.get(a)) for a, v in sorted(expected.items()) if actual.get(a) != v]
    return bad, len(expected)


def parse_num(text, name):
    s = text.strip()
    try:
        return int(s, 0)
    except ValueError:
        try:
            return int(s, 16)
        except ValueError:
            raise ValueError(f"{name} 값을 읽을 수 없습니다: {text!r}")


# ============================================================ 시리얼 통신
class DeviceError(Exception):
    pass


class Cancelled(DeviceError):
    pass


class Link:
    def __init__(self, log):
        self.ser = None
        self.buf = bytearray()
        self.log = log

    @property
    def connected(self):
        return self.ser is not None

    def open(self, port):
        self.close()
        self.ser = serial.serial_for_url(port, baudrate=BAUD, timeout=0.1)
        self.buf.clear()
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def send(self, line):
        self.log(">> " + line)
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()

    def read_line(self, timeout):
        end = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line = bytes(self.buf[:i + 1]).decode("utf-8", "replace").strip()
                del self.buf[:i + 1]
                return line
            if time.monotonic() > end:
                return None
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self.buf += chunk

    def wait_for(self, pred, timeout, what):
        """pred(line) 가 참인 줄이 올 때까지 읽는다. 줄이 timeout 초 동안 안 오면 실패."""
        while True:
            line = self.read_line(timeout)
            if line is None:
                raise DeviceError(f"{what}: {timeout:g}초 동안 응답이 없습니다. 연결과 전원을 확인하세요.")
            if line:
                self.log("<< " + line)
                if pred(line):
                    return line

    def drain(self):
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        self.buf.clear()

    # ---- 명령
    def ping(self):
        """READ 한 번의 왕복 시간(ms). 통신 확인용."""
        t0 = time.monotonic()
        self.read_byte(0)
        return (time.monotonic() - t0) * 1000

    def read_byte(self, addr):
        self.drain()
        self.send(f"READ,0x{addr:04X}")
        line = self.wait_for(lambda l: l.startswith("Address 0x"), 3, "READ")
        m = re.match(r"Address 0x([0-9A-F]+): 0x([0-9A-F]{2})", line, re.I)
        if not m:
            raise DeviceError(f"READ 응답을 해석하지 못했습니다: {line}")
        return int(m.group(2), 16)

    def write_byte(self, addr, data):
        self.drain()
        self.send(f"WRITE,0x{addr:04X},0x{data:02X}")
        time.sleep(0.12)         # 펌웨어는 WRITE 에 응답하지 않으므로 쓰기 시간만큼 기다린다

    def dump(self, lo, hi, progress=None, cancel=None):
        """HEXDUMP 로 [lo, hi] 를 읽어서 dict 로 돌려준다."""
        self.drain()
        self.send(f"HEXDUMP,0x{lo:04X},0x{hi:04X}")
        image, total = {}, hi - lo + 1
        while True:
            line = self.wait_for(lambda l: l.startswith(":") or l.startswith("# End"), 5, "HEXDUMP")
            if line.startswith("# End"):
                break
            rtype, addr, data = parse_ihex_line(line)
            if rtype == 0x01:
                continue
            for i, b in enumerate(data):
                image[addr + i] = b
            if progress:
                progress(len(image), total)
            if cancel and cancel.is_set():
                time.sleep(0.5)          # 펌웨어는 계속 보내므로 잠시 기다렸다가 다음 명령 때 버린다
                raise Cancelled("사용자가 취소했습니다.")
        return image

    def load(self, records, progress=None, cancel=None):
        """LOAD 모드로 레코드를 한 줄씩 보내고, 줄마다 응답을 기다린다 (USB 버퍼가 넘치지 않게)."""
        self.drain()
        self.send("LOAD,0x0000")        # start 값은 펌웨어가 무시함
        self.wait_for(lambda l: l.startswith("Ready to receive"), 3, "LOAD 시작")
        try:
            for n, rec in enumerate(records, 1):
                if cancel and cancel.is_set():
                    raise Cancelled("사용자가 취소했습니다.")
                self.send(rec)
                line = self.wait_for(lambda l: l.startswith("Loaded") or l.startswith("Invalid"), 15, "LOAD 레코드")
                if line.startswith("Invalid"):
                    raise DeviceError(f"펌웨어가 레코드를 거부했습니다: {rec}")
                if progress:
                    progress(n, len(records))
        except Exception:
            self.send("END")              # 어떤 경우에도 LOAD 모드에서 빠져나온다
            time.sleep(0.2)
            raise
        self.send(":00000001FF")
        self.wait_for(lambda l: "Loading completed" in l, 5, "LOAD 종료")

    def erase(self, progress=None):
        self.drain()
        self.send("ERASE")
        while True:
            line = self.wait_for(lambda l: True, 20, "ERASE")
            m = re.match(r"Progress:\s*(\d+)%", line)
            if m and progress:
                progress(int(m.group(1)), 100)
            if "Error erasing" in line:
                raise DeviceError(line)
            if "erase completed" in line:
                return
