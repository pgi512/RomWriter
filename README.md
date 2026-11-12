# ROM Writer

Arduino Pro Micro로 만든 **AT28C64 (8KB) EEPROM 프로그래머**와, 그걸 조작하는 **Windows용 GUI 프로그램**입니다.
Logisim `.hex`, Intel HEX(`.ihx`), `.bin` 파일을 EEPROM에 굽고, 읽고, 비교하고, 바이트 단위로 편집할 수 있습니다.
4bit 컴퓨터의 컨트롤 ROM / 프로그램 ROM을 굽기 위해 만들었습니다.

![ROM Writer](gui/assets/romwriter_256.png)

## 구성

```
RomWriter/
├─ firmware/ROMWriter/   Arduino 펌웨어 (ROMWriter.ino v2.0)
├─ gui/                  PC용 GUI (Python / tkinter) + 자동 테스트 + exe 빌드 스크립트
├─ hardware/             회로도 (jpg, pdf), 부품 목록 (xlsx)
└─ release/              빌드된 ROMWriter.exe (Git에는 올리지 않음 → GitHub Releases에 첨부)
```

## 하드웨어

- Arduino Pro Micro (5V)
- AT28C64B-15PU EEPROM
- 74HC595N 시프트 레지스터 ×2 (주소선)
- 7세그먼트 표시 + 74LS48 ×2 (선택 사항, 상태 표시용)

| Arduino | 연결 | | Arduino | 연결 |
|---|---|---|---|---|
| D14 | SHIFT_DATA | | D10 | EEPROM_WE |
| D15 | SHIFT_LATCH | | A0 | EEPROM_OE |
| D16 | SHIFT_CLK | | D2 ~ D9 | EEPROM D0 ~ D7 |

부품 목록: [`hardware/BOM.xlsx`](hardware/BOM.xlsx)

회로도: [`hardware/schematic.jpg`](hardware/schematic.jpg), [`hardware/AT28C64_programmer_schematic.pdf`](hardware/AT28C64_programmer_schematic.pdf)

## 시작하기

1. `firmware/ROMWriter/ROMWriter.ino`를 Arduino Pro Micro에 업로드합니다.
2. GUI를 실행합니다.
   - **exe**: Releases의 `ROMWriter.exe` (설치 불필요)
   - **소스**: `cd gui` → `pip install -r requirements.txt` → `python romwriter_gui.py` (또는 `run.bat`)
3. 포트를 고르고 **연결** → 읽기 / 쓰기 / 편집 / 도구 탭을 사용합니다.

자세한 사용법, 단축키, 펌웨어 동작상의 주의점, exe 빌드 방법은 **[gui/README.md](gui/README.md)** 를 보세요.

## 테스트

```
cd gui
python selftest.py
```

펌웨어의 시리얼 동작을 흉내 낸 가짜 장치로 쓰기·검증·읽기·편집 등을 확인합니다. (실물 보드는 필요 없음)
테스트용 hex 파일(`fib.hex`, `ctrl_rom1.hex` 등)은 이 저장소에 없고, 기본으로 `../../GyuPuter-4bit/build`를 읽습니다.
다른 곳에 있으면 환경 변수 `ROMWRITER_TEST_BUILD`에 폴더 경로를 지정하세요.

## 상태

- 읽기(`HEXDUMP`)는 실물 보드(COM16, Arduino Leonardo 계열)에서 확인했습니다.
- 쓰기(`LOAD`)는 자동 테스트로만 확인했고 실물 확인은 아직입니다. 처음엔 작은 파일을 구워 검증 결과를 확인하세요.
- 알려진 한계는 [gui/README.md](gui/README.md#알려진-한계)에 있습니다.

## 라이선스

[MIT](LICENSE)
