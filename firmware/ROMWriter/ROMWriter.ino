/*
 * ██████╗  ██████╗ ███╗   ███╗    ██╗    ██╗██████╗ ██╗████████╗███████╗██████╗
 * ██╔══██╗██╔═══██╗████╗ ████║    ██║    ██║██╔══██╗██║╚══██╔══╝██╔════╝██╔══██╗
 * ██████╔╝██║   ██║██╔████╔██║    ██║ █╗ ██║██████╔╝██║   ██║   █████╗  ██████╔╝
 * ██╔══██╗██║   ██║██║╚██╔╝██║    ██║███╗██║██╔══██╗██║   ██║   ██╔══╝  ██╔══██╗
 * ██║  ██║╚██████╔╝██║ ╚═╝ ██║    ╚███╔███╔╝██║  ██║██║   ██║   ███████╗██║  ██║
 * ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝     ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝
 *
 * ================================================================================
 *                    Arduino AT28C64 EEPROM Programmer v2.0
 * ================================================================================
 *
 * 📋 Description:
 *    General AT28C64 EEPROM Programmer with 7-Segment Display
 *    Serial interface-based read/write/dump functionality with Intel HEX support
 *
 * 🔧 Hardware Requirements:
 *    • Arduino Pro Micro (5V)
 *    • AT28C64B-15PU EEPROM (8KB)
 *    • 74HC595N Shift Register (x2)
 *    • 7-Segment Display with 74LS48 BCD Decoder (x2)
 *
 * 📡 Pin Mapping:
 *    D14 → SHIFT_DATA    │ D10 → EEPROM_WE
 *    D15 → SHIFT_LATCH   │ A0  → EEPROM_OE
 *    D16 → SHIFT_CLK     │ D2~D9 → EEPROM D0~D7
 *
 * 💬 Serial Commands:
 *    READ,addr          → Read EEPROM address
 *    WRITE,addr,data    → Write data to address
 *    DUMP[,start,end]   → Memory dump (HEX editor format)
 *    HEXDUMP[,start,end]→ Intel HEX format dump
 *    LOAD,start         → Load Intel HEX data
 *    DISPLAY,number     → Show number on 7-segment
 *    ERASE              → Erase entire EEPROM
 *
 * ⚙️  Settings:
 *    Serial: 115200 baud, 8N1
 *    EEPROM: 8KB (0x0000~0x1FFF)
 *    Timing: 10μs read, 1500μs write
 *
 * 📅 Version History:
 *    v2.0 - Fixed com_ind type issue, improved error handling
 *    v1.0 - Initial release with basic functionality
 *
 * 👨‍💻 Author: Gyuil.Park
 * 📧 Contact: pgi512@naver.com
 * 🔗 GitHub: https://github.com/[username]/arduino-eeprom-programmer
 * 📄 License: MIT License
 *
 * ================================================================================
 */

#include <stdint.h>
#include <avr/io.h>

// IO Definitions
#define SHIFT_DATA 14            // 시프트 레지스터 데이터 핀
#define SHIFT_LATCH 15           // 시프트 레지스터 래치 핀
#define SHIFT_CLK 16             // 시프트 레지스터 클럭 핀
#define EEP_WE 10                // EEPROM Write Enable
#define EEP_OE A0                // EEPROM Output Enable

// EEPROME Definitions
#define ADDWIDTH 13              // EEPROM 13bit addressing in AT28C64
#define EEPROM_SIZE 8192         // AT28C64 전체 크기 (8KB)
#define READ_DELAY 10            // EEPROM 읽기 지연시간 (마이크로초)
#define WRITE_DELAY 1500         // EEPROM 쓰기 지연시간 (마이크로초)
#define WRITE_RETRY_COUNT 5      // EEPROM 쓰기 재시도 횟수

// Com Settings
#define SERIAL_BAUD 115200UL

// Golbal variabels
const uint8_t iopin[8] = { 2, 3, 4, 5, 6, 7, 8, 9 };    // DATA0 ~ 7

//-------------------------------------- Arduin Function --------------------------------------//
void ioOut(void) {
  for (byte j = 0; j < 8; j++)
    pinMode(iopin[j], OUTPUT);
}

void ioIn(void) {
  for (byte j = 0; j < 8; j++)
    pinMode(iopin[j], INPUT);
}

//-------------------------------------- EEPROM Function --------------------------------------//

void setAddress(uint16_t address) {
  digitalWrite(SHIFT_LATCH, LOW);
  shiftOut(SHIFT_DATA, SHIFT_CLK, MSBFIRST, byte(address >> 8));
  shiftOut(SHIFT_DATA, SHIFT_CLK, MSBFIRST, byte(address));
  digitalWrite(SHIFT_LATCH, HIGH);
}

void loadData(uint8_t input) {
  ioOut();
  
  for (uint8_t i = 0; i < 8; i++) {
    if (input & 0x01)
      digitalWrite(iopin[i], HIGH);
    else
      digitalWrite(iopin[i], LOW);
    input = input >> 1;
  }
}

uint8_t readEEPROM(uint16_t address) {
  ioIn();
  setAddress(address);
  digitalWrite(EEP_OE, LOW);        // Active LOW
  delayMicroseconds(READ_DELAY);

  uint8_t data = 0x00;
  for (uint8_t i = 0; i < 8; i++)
    data = data + (uint8_t(digitalRead(iopin[i])) << i);
  
  return data;
}

bool writeEEPROM(uint16_t address, uint8_t data) {
  uint8_t originalData = data;
  
  for (uint8_t attempt = 0; attempt < WRITE_RETRY_COUNT; attempt++) {
    digitalWrite(EEP_OE, HIGH);     // disable output enable pin
    
    ioOut();    // change arduino i/o port to ready for writing
    setAddress(address);            // move to intended addr
    loadData(originalData);         // load data to i/o port
    
    digitalWrite(EEP_WE, LOW);
    delayMicroseconds(WRITE_DELAY);
    digitalWrite(EEP_WE, HIGH);
    delayMicroseconds(WRITE_DELAY);
    
    uint8_t readBack = readEEPROM(address);     // Write VFY
    
    if (readBack == originalData) {     // if write success
      return 0; // Success
    } else {    // Failure
      if (attempt < WRITE_RETRY_COUNT - 1)
        delayMicroseconds(5);       // Wait before Re-Write
    }
  }
  return 1;     // Write Failure.
}

//-------------------------------------- HEX File Handle --------------------------------------//

void dumpEEPROM(uint16_t startAddr, uint16_t endAddr) {

  Serial.println(F("EEPROM Memory Dump"));
  Serial.println(F("=================="));

  for (uint16_t addr = startAddr; addr <= endAddr; addr += 16) {
    // 주소 출력 (4자리 16진수)
    if (addr < 0x1000) Serial.print(F("0"));
    if (addr < 0x100) Serial.print(F("0"));
    if (addr < 0x10) Serial.print(F("0"));
    Serial.print(addr, HEX);
    Serial.print(F(": "));
    
    // 첫 번째 8바이트 출력
    for (int i = 0; i < 8 && (addr + i) <= endAddr; i++) {
      byte data = readEEPROM(addr + i);
      if (data < 0x10) Serial.print(F("0"));
      Serial.print(data, HEX);
      Serial.print(F(" "));
    }
    
    // 중간 공백 (8바이트씩 구분)
    Serial.print(F(" "));
    
    // 두 번째 8바이트 출력
    for (int i = 8; i < 16 && (addr + i) <= endAddr; i++) {
      byte data = readEEPROM(addr + i);
      if (data < 0x10) Serial.print(F("0"));
      Serial.print(data, HEX);
      Serial.print(F(" "));
    }
    
    Serial.println(); // 다음 줄로
  }
  
  Serial.println(F("=================="));
  Serial.print(F("Address range: 0x"));
  if (startAddr < 0x1000) Serial.print(F("0"));
  if (startAddr < 0x100) Serial.print(F("0"));
  if (startAddr < 0x10) Serial.print(F("0"));
  Serial.print(startAddr, HEX);
  Serial.print(F(" to 0x"));
  if (endAddr < 0x1000) Serial.print(F("0"));
  if (endAddr < 0x100) Serial.print(F("0"));
  if (endAddr < 0x10) Serial.print(F("0"));
  Serial.print(endAddr, HEX);
  Serial.print(F(" ("));
  Serial.print(endAddr - startAddr + 1);
  Serial.println(F(" bytes)"));
}

void dumpEEPROMAsHex(int startAddr, int endAddr) {
  Serial.println(F("# Intel HEX Format"));
  Serial.print(F("# Address range: 0x"));
  Serial.print(startAddr, HEX);
  Serial.print(F(" to 0x"));
  Serial.println(endAddr, HEX);
  
  for (int addr = startAddr; addr <= endAddr; addr += 16) {
    int lineLength = min(16, endAddr - addr + 1);
    
    // Intel HEX 레코드 생성
    Serial.print(F(":"));
    
    // 바이트 카운트 (2자리 HEX)
    if (lineLength < 16) Serial.print(F("0"));
    Serial.print(lineLength, HEX);
    
    // 주소 (4자리 HEX)
    if (addr < 0x1000) Serial.print(F("0"));
    if (addr < 0x100) Serial.print(F("0"));
    if (addr < 0x10) Serial.print(F("0"));
    Serial.print(addr, HEX);
    
    // 레코드 타입 (00 = 데이터)
    Serial.print(F("00"));
    
    // 체크섬 계산을 위한 변수
    byte checksum = lineLength + ((addr >> 8) & 0xFF) + (addr & 0xFF) + 0x00;
    
    // 데이터 바이트들
    for (int i = 0; i < lineLength; i++) {
      byte data = readEEPROM(addr + i);
      if (data < 0x10) Serial.print(F("0"));
      Serial.print(data, HEX);
      checksum += data;
    }
    
    // 체크섬 (2의 보수)
    checksum = (~checksum + 1) & 0xFF;
    if (checksum < 0x10) Serial.print(F("0"));
    Serial.print(checksum, HEX);
    
    Serial.println();
  }
  
  // 종료 레코드
  Serial.println(F(":00000001FF"));
  Serial.println(F("# End of Intel HEX"));
}

void loadHEXData(int startAddr) {
  Serial.println(F("Ready to receive HEX data. Send ':' terminated lines."));
  Serial.println(F("Send 'END' when finished."));
  
  bool loading = true;
  
  while (loading) {
    if (Serial.available()) {
      String hexLine = Serial.readStringUntil('\n');
      hexLine.trim();
      hexLine.toUpperCase();
      
      if (hexLine == "END") {
        loading = false;
        Serial.println(F("HEX loading completed."));
        continue;
      }
      
      if (hexLine.length() < 11 || hexLine.charAt(0) != ':') {
        Serial.println(F("Invalid HEX line format"));
        continue;
      }
      
      // HEX 라인 파싱
      int byteCount = hexStringToInt(hexLine.substring(1, 3));
      int address = hexStringToInt(hexLine.substring(3, 7));
      int recordType = hexStringToInt(hexLine.substring(7, 9));
      
      if (recordType == 0x01) {  // 종료 레코드
        loading = false;
        Serial.println(F("End record received. Loading completed."));
        continue;
      }
      
      if (recordType != 0x00) {  // 데이터 레코드가 아님
        continue;
      }
      
      // 데이터 추출 및 쓰기
      for (int i = 0; i < byteCount; i++) {
        int dataStart = 9 + (i * 2);
        byte data = hexStringToInt(hexLine.substring(dataStart, dataStart + 2));
        writeEEPROM(address + i, data);
      }
      
      Serial.print(F("Loaded "));
      Serial.print(byteCount);
      Serial.print(F(" bytes at address 0x"));
      Serial.println(address, HEX);
    }
  }
}

int hexStringToInt(String hexStr) {
  int result = 0;
  for (int i = 0; i < hexStr.length(); i++) {
    char c = hexStr.charAt(i);
    result *= 16;
    if (c >= '0' && c <= '9') {
      result += c - '0';
    } else if (c >= 'A' && c <= 'F') {
      result += c - 'A' + 10;
    }
  }
  return result;
}

int parseValue(String str) {
  str.trim();
  str.toUpperCase();
  
  // HEX 값인지 확인 (0x 접두사 또는 A-F 포함)
  if (str.startsWith("0X")) {
    // 0x 접두사가 있는 경우
    return hexStringToInt(str.substring(2));
  } else {
    // A-F가 포함되어 있으면 HEX로 처리
    bool isHex = false;
    for (int i = 0; i < str.length(); i++) {
      char c = str.charAt(i);
      if (c >= 'A' && c <= 'F') {
        isHex = true;
        break;
      }
    }
    
    if (isHex) {
      return hexStringToInt(str);
    } else {
      return str.toInt();  // 10진수로 처리
    }
  }
}

void eraseEEPROM() {
    Serial.println(F("Starting EEPROM erase..."));
    Serial.print(F("Total size: "));
    Serial.print(EEPROM_SIZE);
    Serial.println(F(" bytes"));
    
    uint8_t lastProgress = 0;
    
    for (uint16_t addr = 0; addr < EEPROM_SIZE; addr++) {
        if (writeEEPROM(addr, 0xFF)) {
            Serial.print(F("Error erasing address 0x"));
            Serial.println(addr, HEX);
            return;  // 오류 발생 시 중단
        }
        
        // 1%씩 진행률 업데이트
        uint8_t currentProgress = (uint32_t(addr) * 100) / EEPROM_SIZE;
        if (currentProgress != lastProgress || addr == EEPROM_SIZE - 1) {
            Serial.print(F("Progress: "));
            Serial.print(currentProgress);
            Serial.print(F("% ("));
            Serial.print(addr + 1);
            Serial.print(F("/"));
            Serial.print(EEPROM_SIZE);
            Serial.println(F(" bytes)"));
            lastProgress = currentProgress;
        }
    }
    
    Serial.println(F("✅ EEPROM erase completed successfully!"));
}

//-------------------------------------- Segment Display --------------------------------------//

void displayNumber(uint8_t number) {
  digitalWrite(EEP_OE, HIGH);  // Disable EEPROM(Active LOW)
  loadData(number);
}

//-------------------------------------- Arduino Main Code --------------------------------------//

void setup() {
  // PinMode Settings
  pinMode(EEP_OE, OUTPUT);
  pinMode(EEP_WE, OUTPUT);
  pinMode(SHIFT_CLK, OUTPUT);
  pinMode(SHIFT_DATA, OUTPUT);
  pinMode(SHIFT_LATCH, OUTPUT);
  
  // 초기 상태 설정
  digitalWrite(EEP_WE, HIGH);
  digitalWrite(EEP_OE, HIGH);
  
  Serial.begin(SERIAL_BAUD);
  delay(2000);
  
  Serial.println(F("========================================"));
  Serial.println(F("AT28C64 EEPROM Programmer v2.0"));
  Serial.println(F("========================================"));
  Serial.println();
  Serial.println(F("Commands:"));
  Serial.println(F("READ,address          - Read EEPROM"));
  Serial.println(F("WRITE,address,data    - Write EEPROM"));
  Serial.println(F("DISPLAY,number        - Display number (0-99)"));
  Serial.println(F("SHOW,address          - Show data from EEPROM address"));
  Serial.println(F("DUMP[,start,end]      - Dump EEPROM range (HEX editor format)"));
  Serial.println(F("HEXDUMP[,start,end]   - Dump EEPROM range (Intel HEX format)"));
  Serial.println(F("LOAD,start            - Load HEX data starting from address"));
  Serial.println(F("ERASE                 - Erase entire EEPROM (fill with 0xFF)"));
  Serial.println();
}

void loop() {
  if (Serial.available()) {
    String user_input = Serial.readStringUntil('\n');
    user_input.toUpperCase();
    user_input.trim();
    
    int com_ind = user_input.indexOf(',');
    String command;
    
    if (com_ind == -1) {
      command = user_input;  // 콤마가 없는 경우 전체가 명령어
    } else {
      command = user_input.substring(0, com_ind);
    }
    
    if (command == "READ") {
      int address = parseValue(user_input.substring(com_ind + 1));
      byte data = readEEPROM(address);
      Serial.print(F("Address 0x"));
      if (address < 0x1000) Serial.print(F("0"));
      if (address < 0x100) Serial.print(F("0"));
      if (address < 0x10) Serial.print(F("0"));
      Serial.print(address, HEX);
      Serial.print(F(": 0x"));
      if (data < 0x10) Serial.print(F("0"));
      Serial.print(data, HEX);
      Serial.print(F(" (DEC: "));
      Serial.print(data);
      Serial.println(F(")"));
      
    } else if (command == "WRITE") {
      int second_param_ind = user_input.indexOf(',', com_ind + 1);
      int address = parseValue(user_input.substring(com_ind + 1, second_param_ind));
      byte data = parseValue(user_input.substring(second_param_ind + 1));
      writeEEPROM(address, data);
      
    } else if (command == "DISP") {
      uint8_t number = parseValue(user_input.substring(com_ind + 1));
      displayNumber(number);
      
    } else if (command == "DUMP") {
      if (com_ind == -1) {
        // 파라미터 없음: 전체 EEPROM 덤프 (0 ~ 8191)
        Serial.println(F("Executing full EEPROM dump..."));
        dumpEEPROM(0, EEPROM_SIZE - 1);
      } else {
        // 파라미터 있음: start,end 범위 덤프
        int second_param_ind = user_input.indexOf(',', com_ind + 1);
        if (second_param_ind == -1) {
          Serial.println(F("Usage: DUMP,start,end"));
        } else {
          int startAddr = parseValue(user_input.substring(com_ind + 1, second_param_ind));
          int endAddr = parseValue(user_input.substring(second_param_ind + 1));
          dumpEEPROM(startAddr, endAddr);
        }
      }
      
    } else if (command == "HEXDUMP") {
      if (com_ind == -1) {
        // 파라미터 없음: 전체 EEPROM 덤프 (0 ~ 8191)
        dumpEEPROMAsHex(0, EEPROM_SIZE - 1);
      } else {
        // 파라미터 있음: start,end 범위 덤프
        int second_param_ind = user_input.indexOf(',', com_ind + 1);
        if (second_param_ind == -1) {
          Serial.println(F("Usage: HEXDUMP,start,end"));
        } else {
          int startAddr = parseValue(user_input.substring(com_ind + 1, second_param_ind));
          int endAddr = parseValue(user_input.substring(second_param_ind + 1));
          dumpEEPROMAsHex(startAddr, endAddr);
        }
      }
      
    } else if (command == "LOAD") {
      int startAddr = parseValue(user_input.substring(com_ind + 1));
      loadHEXData(startAddr);
      
    } else if (command == "ERASE") {
      // Serial.println(F("Are you sure? This will erase entire EEPROM. Type 'YES' to confirm:"));
      // while (!Serial.available()) { delay(10); }
      // String confirm = Serial.readStringUntil('\n');
      // confirm.trim();
      // confirm.toUpperCase();
      // if (confirm == "YES") {
      //   eraseEEPROM();
      // } else {
      //   Serial.println(F("Erase cancelled"));
      // }
      eraseEEPROM();
    } else {
      Serial.println(F("Unknown command. Available commands:"));
      Serial.println(F("READ,address | WRITE,address,data | DISPLAY,number"));
      Serial.println(F("SHOW,address | DUMP[,start,end] | HEXDUMP[,start,end]"));
      Serial.println(F("LOAD,start | ERASE"));
    }
  }
}
