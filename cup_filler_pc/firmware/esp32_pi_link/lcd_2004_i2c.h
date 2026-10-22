// ===========================================================================
//  lcd_2004_i2c.h - LCD 2004 (HD44780 + PCF8574 I2C backpack) cho ESP32
//
//  * Khong can cai them thu vien LiquidCrystal_I2C: chi dung Wire.h cua
//    Arduino-ESP32.
//  * Tu do dia chi 0x27 / 0x3F (hai dia chi pho bien cua module PCF8574).
//  * Chi ghi lai dong nao thay doi, nen khong lam nhap nhay man hinh va
//    khong chiem bus I2C lien tuc.
//  * Neu khong thay LCD, begin() tra false va firmware van chay binh thuong.
//
//  Day noi mac dinh: SDA=GPIO21, SCL=GPIO22 (xem pi_config.h).
//  Canh bao dien ap: nhieu backpack LCD dung VCC 5V va keo SDA/SCL len 5V.
//  GPIO ESP32 khong chiu 5V; hay dung level-shifter I2C hai chieu hoac dam
//  bao dien tro keo I2C nam o phia 3V3.
// ===========================================================================
#ifndef CUPFILLER_LCD_2004_I2C_H
#define CUPFILLER_LCD_2004_I2C_H

#include <Arduino.h>
#include <Wire.h>
#include <string.h>

class Lcd2004 {
 public:
  static constexpr uint8_t COLS = 20;
  static constexpr uint8_t ROWS = 4;

  // forcedAddress = 0: tu do 0x27 roi 0x3F.
  bool begin(int sdaPin, int sclPin, uint32_t frequencyHz = 100000,
             uint8_t forcedAddress = 0) {
    _available = false;
    _address = 0;
    memset(_lines, 0, sizeof(_lines));
    memset(_lineValid, 0, sizeof(_lineValid));

    if (frequencyHz == 0) frequencyHz = 100000;
    // PCF8574 thuong chi dam bao 100 kHz; khong ep len 400 kHz.
    if (frequencyHz > 100000) frequencyHz = 100000;

    Wire.begin(sdaPin, sclPin, frequencyHz);

    if (forcedAddress != 0) {
      if (!_probe(forcedAddress)) return false;
      _address = forcedAddress;
    } else {
      static const uint8_t candidates[] = {0x27, 0x3F};
      for (uint8_t i = 0; i < sizeof(candidates); i++) {
        if (_probe(candidates[i])) {
          _address = candidates[i];
          break;
        }
      }
      if (_address == 0) return false;
    }

    _available = true;
    _backlight = true;

    // Khoi tao HD44780 o che do 4-bit theo datasheet.
    delay(50);
    if (!_writeNibble(0x03, false)) return _fail();
    delay(5);
    if (!_writeNibble(0x03, false)) return _fail();
    delayMicroseconds(150);
    if (!_writeNibble(0x03, false)) return _fail();
    if (!_writeNibble(0x02, false)) return _fail();

    if (!_command(0x28)) return _fail();  // 4-bit, 2 dong logic, font 5x8
    if (!_command(0x0C)) return _fail();  // display ON, cursor/blink OFF
    if (!clear()) return false;
    if (!_command(0x06)) return _fail();  // tang con tro, khong dich man hinh
    return true;
  }

  bool available() const { return _available; }
  uint8_t address() const { return _address; }

  void setBacklight(bool on) {
    _backlight = on;
    if (_available) {
      uint8_t value = on ? LCD_BACKLIGHT : 0;
      if (!_writeExpander(value)) _available = false;
    }
    // Lan ghi tiep theo can ve lai vi trang thai den nen da doi.
    memset(_lineValid, 0, sizeof(_lineValid));
  }

  bool clear() {
    if (!_available) return false;
    if (!_command(0x01)) return _fail();
    delay(2);
    memset(_lines, 0, sizeof(_lines));
    memset(_lineValid, 0, sizeof(_lineValid));
    return true;
  }

  // Ghi mot dong; text se bi cat/dem khoang trang thanh dung 20 ky tu.
  bool printLine(uint8_t row, const char *text) {
    if (!_available || row >= ROWS) return false;
    if (text == NULL) text = "";

    char padded[COLS + 1];
    size_t n = strlen(text);
    if (n > COLS) n = COLS;
    memcpy(padded, text, n);
    for (size_t i = n; i < COLS; i++) padded[i] = ' ';
    padded[COLS] = '\0';

    if (_lineValid[row] && strcmp(_lines[row], padded) == 0) {
      return true;
    }

    // 1 lenh set-cursor + 20 ky tu; moi byte LCD can 4 byte PCF8574.
    uint8_t seq[(COLS + 1) * 4];
    size_t pos = 0;
    _appendByte(seq, pos, (uint8_t)(0x80 | _rowOffset(row)), false);
    for (uint8_t col = 0; col < COLS; col++) {
      _appendByte(seq, pos, (uint8_t)padded[col], true);
    }
    if (!_writeSequence(seq, pos)) return _fail();

    memcpy(_lines[row], padded, COLS + 1);
    _lineValid[row] = true;
    return true;
  }

  bool show(const char *line0, const char *line1, const char *line2,
            const char *line3) {
    return printLine(0, line0) && printLine(1, line1) &&
           printLine(2, line2) && printLine(3, line3);
  }

 private:
  static constexpr uint8_t LCD_RS = 0x01;         // PCF8574 P0
  static constexpr uint8_t LCD_EN = 0x04;         // PCF8574 P2
  static constexpr uint8_t LCD_BACKLIGHT = 0x08;  // PCF8574 P3

  bool _probe(uint8_t address) {
    Wire.beginTransmission(address);
    return Wire.endTransmission() == 0;
  }

  bool _fail() {
    _available = false;
    return false;
  }

  uint8_t _rowOffset(uint8_t row) const {
    switch (row) {
      case 1: return 0x40;
      case 2: return 0x14;
      case 3: return 0x54;
      default: return 0x00;
    }
  }

  uint8_t _expanderByte(uint8_t nibble, bool data, bool enable) const {
    uint8_t value = (uint8_t)((nibble & 0x0F) << 4);  // D4..D7 = P4..P7
    if (data) value |= LCD_RS;
    if (enable) value |= LCD_EN;
    if (_backlight) value |= LCD_BACKLIGHT;
    return value;
  }

  void _appendNibble(uint8_t *seq, size_t &pos, uint8_t nibble,
                     bool data) const {
    seq[pos++] = _expanderByte(nibble, data, true);
    seq[pos++] = _expanderByte(nibble, data, false);
  }

  void _appendByte(uint8_t *seq, size_t &pos, uint8_t value,
                   bool data) const {
    _appendNibble(seq, pos, (uint8_t)(value >> 4), data);
    _appendNibble(seq, pos, (uint8_t)(value & 0x0F), data);
  }

  bool _writeSequence(const uint8_t *seq, size_t len) {
    if (!_available || _address == 0 || seq == NULL || len == 0) return false;
    Wire.beginTransmission(_address);
    size_t written = Wire.write(seq, len);
    uint8_t result = Wire.endTransmission();
    return written == len && result == 0;
  }

  bool _writeExpander(uint8_t value) {
    return _writeSequence(&value, 1);
  }

  bool _writeNibble(uint8_t nibble, bool data) {
    uint8_t seq[2];
    size_t pos = 0;
    _appendNibble(seq, pos, nibble, data);
    return _writeSequence(seq, pos);
  }

  bool _writeByte(uint8_t value, bool data) {
    uint8_t seq[4];
    size_t pos = 0;
    _appendByte(seq, pos, value, data);
    return _writeSequence(seq, pos);
  }

  bool _command(uint8_t value) { return _writeByte(value, false); }

  bool _available = false;
  bool _backlight = true;
  uint8_t _address = 0;
  char _lines[ROWS][COLS + 1];
  bool _lineValid[ROWS];
};

#endif  // CUPFILLER_LCD_2004_I2C_H
