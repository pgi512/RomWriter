# ROM Writer (AT28C64 programmer GUI for PC)

Talks over serial (115200 baud) to the Arduino Pro Micro programmer (`../firmware/ROMWriter/ROMWriter.ino` v2.0, firmware left unmodified) to
burn, read, compare, and byte-edit `.hex` (Logisim) / `.ihx` (Intel HEX) / `.bin` files on the EEPROM.
Used to burn GyuPuter-4bit's `build/*.hex` (two control ROMs and the program ROM).

> The interface language is Korean. Korean labels are given in parentheses below where helpful.

## Running

| Method | Description |
|---|---|
| `ROMWriter.exe` | Single file, no install. Runs without Python. |
| `run.bat` / `python romwriter_gui.py` | Run from source (requires Python 3 + `pip install pyserial`) |

> **Note: program execution is blocked on `I:` (NAS).** (Even a copy of `whoami.exe` on `I:` fails with "Access is denied" — confirmed 2026-11-09.)
> Keep the `.exe` on a **local drive (C:, D:)** and run it from there. In this project it lives at `D:\ROMWriter\ROMWriter.exe`.
> The "access denied" error seen earlier with `run.bat` may have the same cause (not confirmed).

## Screen layout

- **Ribbon (top)**: Read (읽기) / Write (쓰기) / Edit (편집) / Tools (도구) tabs (Ctrl+1~4). The selected tab's toolbar sits right below. Each tab has its own color.
- **Connection bar (left-aligned)**: port selector, refresh, connect/disconnect, connection status. Arduino ports are auto-selected first.
- **Status strip**: shows the current operation (reading / writing / verifying / erasing) by color and text, plus progress, time remaining, and the result (done / problem).
- **Selected byte (left)**: address, value (hex input), D7~D0 bit toggles, decimal/binary/character, selection range, whether it matches the EEPROM, and read/write for just that byte.
- **Data view (center)**: hex editor. The **Serial log** (통신 로그) tab shows the serial traffic.
- Menu bar: File (open, recent files, save), Edit, Device, View (mode, theme, bytes per row), Help. Themes: light / dark / follow system.

## Workflow

1. Pick a port and **Connect** (연결).
2. **Read**: choose a range and press `F5`. The EEPROM is not modified.
3. **Write**: opening a file previews it in the table (nothing is written yet). **Burn file** (파일 굽기) writes it and then **automatically reads it back and compares**. Mismatched cells are shown in pink, and bad bits are reported like `DATA3`.
4. **Edit**: values changed in the table turn yellow (changes not yet written to the EEPROM). **Write changes** (변경분 쓰기) writes and verifies only the yellow cells.
5. **Tools**: communication check, blank check (reads 8KB), checksum of the table (sum/XOR/CRC32), erase all.

## Using the data view (hex editor)

| Action | How |
|---|---|
| Change a value | Click a hex cell and type two hex digits |
| Type as text | Click the ASCII column on the right and type (non-ASCII such as Hangul goes in as multiple UTF-8 bytes). `F2` switches columns |
| Select a range | Drag, or Shift+click / Shift+arrow keys. **Dragging copies to the clipboard immediately** |
| Copy / paste | Ctrl+C / Ctrl+V (from the start of the selection). In the hex column, `DE AD BE EF`, `0x10,0x20`, `DEADBEEF` are parsed as hex, anything else as text. In the ASCII column, always as text |
| Undo | Ctrl+Z (a paste or range fill is undone as one step) |
| Navigate | Arrow keys, PageUp/Down, Home/End, go to address (Ctrl+G) |
| Displayed range | Only up to what was read (or is in the file). Going to an address outside the table extends it to that address. A new read shrinks it again |
| Bytes per row | `Auto` (fit to window width) / 8 / 16 / 32 / 64 |

Shortcuts: `Ctrl+O` open · `Ctrl+S` save table · `F5` read · `Ctrl+Z` undo · `Ctrl+G` go to address · `Ctrl+1~4` switch mode · `F2` switch input column

## Firmware quirks to know (confirmed by reading ROMWriter.ino)

- The `start` value in `LOAD,start` is **ignored**; addresses come from the HEX records themselves. → This program sends records with the offset already added.
- The firmware **answers "Loaded N bytes" even when a write fails.** → So after burning, the data is always read back and compared (with verification off, failures go unnoticed).
- Bare numbers are parsed as decimal. → Addresses are always sent with a `0x` prefix.
- The firmware verifies by reading only 1.5ms after a write. If the AT28C64B's write cycle (tWC) is longer than that (up to about 10ms, as far as I remember), data polling may cause a wrong read. Needs checking against the datasheet (a firmware-side issue).

## Verification status (2026-11-09)

- **Self-test passes** (`python selftest.py`): using a fake device (TCP socket) that mimics the firmware's serial behavior, it covers file format parsing, burn + verify, read, edit, range extension, text input, drag-copy, paste, erase, reporting a verify failure when one data line is broken, dark mode switching, and layout placement.
- **Verified on hardware**: 8KB read (`HEXDUMP`) succeeded on COM16 (Arduino Leonardo); the EEPROM was all FF.
- **Not yet verified on hardware**: **writing (`LOAD`)**. For the first write, burn `build/ctrl_rom1.hex` at `0x0000` and check the verification result.
- Visual checks of the UI were done only from screenshots sent by the user (changes made after that were not checked directly).

## Known limitations

- The menu bar and drop-down menus use the Windows default colors and do not change in dark mode (the title bar does go dark).
- The ASCII column shows only 0x20~0x7E as characters; everything else (e.g. UTF-8 bytes of Hangul) appears as `.`.
- Whether clipboard contents survive after the app exits has not been checked.
- The `.exe` does not include the `socket://` address handling used for fake-device testing (no effect on real COM ports).
- An erase cannot be stopped once started (a consequence of the firmware's structure).
- Only one operation at a time can talk to the board.

## Files

| File | Contents |
|---|---|
| `romwriter_gui.py` | UI (mode tabs, status strip, hex editor, menus, themes) |
| `romwriter_core.py` | UI-independent parts: file format parsing, serial communication, comparison |
| `selftest.py` | Automated tests against a fake device |
| `make_icon.py`, `assets/` | App icon (DIP chip shape, generated with Pillow) |
| `build_exe.bat` | `.exe` build script (see below) |
| `run.bat`, `requirements.txt` | For running from source |
| `dist/ROMWriter.exe` | Archived copy of the build output (not tracked in Git; cannot run from the NAS, run it from `D:\ROMWriter`) |

Settings are stored in `%APPDATA%\ROMWriterGUI\settings.json` (last port, recent files, theme, bytes per row); nothing is written next to the executable.

## Rebuilding the .exe

1. **Copy this folder to a local drive (D:, etc.)** (build output cannot be executed from the NAS).
2. Run `build_exe.bat` in the copied folder (requires Python 3 and internet access — it installs PyInstaller and pyserial).
3. Output: `dist\ROMWriter.exe` (about 13MB, no console window, icon included, single file).

Note: an unsigned `.exe` built with PyInstaller may start slowly the first time (it unpacks itself) and may be flagged as suspicious by antivirus software.
