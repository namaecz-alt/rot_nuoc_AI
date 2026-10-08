// ===========================================================================
//  arduino_stub.h - GIẢ LẬP ARDUINO/ESP32 ĐỦ ĐỂ *KIỂM TRA CÚ PHÁP* FIRMWARE
//
//  Máy ảo này KHÔNG chạy được firmware thật: mọi hàm đều là hàm rỗng. Mục đích
//  duy nhất là để `g++ -fsyntax-only` phát hiện lỗi biên dịch (sai tên hàm, sai
//  kiểu, thiếu dấu chấm phẩy...) trước khi bạn mở Arduino IDE, vì trong môi
//  trường này không có trình biên dịch xtensa-esp32.
//
//  Dùng: xem tools/test_comms.py (nhóm kiểm tra "firmware").
// ===========================================================================
#ifndef CUPFILLER_ARDUINO_STUB_H
#define CUPFILLER_ARDUINO_STUB_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define HIGH 0x1
#define LOW  0x0
#define INPUT        0x01
#define OUTPUT       0x03
#define INPUT_PULLUP 0x05
#define SERIAL_8N1   0x800001c
#define DEC 10
#define HEX 16
#define OCT 8
#define BIN 2
#define ESP_INTR_FLAG_LEVEL1 1
#define I2S_PIN_NO_CHANGE (-1)
#define pdMS_TO_TICKS(x) (x / portTICK_PERIOD_MS)
static const int portTICK_PERIOD_MS = 1;

#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif
#ifndef max
#define max(a, b) ((a) > (b) ? (a) : (b))
#endif

typedef int esp_err_t;
static const esp_err_t ESP_OK = 0;

inline uint32_t millis() { return 0; }
inline uint32_t micros() { return 0; }
inline void delay(uint32_t) {}
inline void delayMicroseconds(uint32_t) {}
inline void pinMode(int, int) {}
inline int  digitalRead(int) { return HIGH; }
inline void digitalWrite(int, int) {}
inline int  analogRead(int) { return 2048; }
inline void analogReadResolution(int) {}
inline uint32_t pulseIn(int, int, uint32_t = 1000000UL) { return 0; }

// ---- Serial giả: để sketch gọi print/println thoải mái --------------------
class StubSerial {
 public:
  void begin(unsigned long) {}
  int available() { return 0; }
  int availableForWrite() { return 256; }
  int read() { return -1; }
  size_t write(const uint8_t *, size_t n) { return n; }
  size_t write(uint8_t) { return 1; }
  template <class T> void print(T) {}
  template <class T> void print(T, int) {}
  void print(const char *) {}
  void print(const char *, int) {}
  template <class T> void println(T) {}
  template <class T> void println(T, int) {}
  void println() {}
  void println(const char *) {}
  void flush() {}
};

class HardwareSerial : public StubSerial {
 public:
  void begin(unsigned long, uint32_t = 0, int = -1, int = -1) {}
  void setRxBufferSize(size_t) {}
  void setTxBufferSize(size_t) {}
};

extern StubSerial Serial;
extern HardwareSerial Serial2;

#endif  // CUPFILLER_ARDUINO_STUB_H
