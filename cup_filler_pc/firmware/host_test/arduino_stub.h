// ===========================================================================
//  arduino_stub.h - MÁY ẢO MINI ARDUINO/ESP32 CHO MÁY TÍNH (chạy thật, không cần bo)
//
//  CÓ HAI MỤC ĐÍCH:
//    1. `g++ -fsyntax-only`  -> kiểm tra cú pháp firmware mà không cần xtensa-esp32
//       (xem tools/test_comms.py, nhóm [6]).
//    2. Chạy THẬT logic firmware trên máy tính (xem test_firmware_run.cpp):
//       - đồng hồ ảo (millis/micros/delay)
//       - chân GPIO có mức thật: nút bấm kéo xuống LOW, đọc được mức RELAY
//       - HC-SR04: pulseIn trả thời gian ứng với khoảng cách cách đặt trước
//       - mic analog: tự sinh tín hiệu để thử VAD/đếm "tiếng"
//       - UART2: bytes firmware gửi ra được thu vào đệm, bytes PC gửi xuống thì nạp vào RX
//
//  Nhờ vậy test_firmware_run.cpp kiểm tra được ĐÚNG mã nguồn của .ino: nút còn khoá thì
//  bấm không ăn, có CUP_OK mới mở khoá, relay tích cực mức CAO, mất cốc/ mất liên lạc thì
//  ngắt bơm... mà không cần phần cứng.
// ===========================================================================
#ifndef CUPFILLER_ARDUINO_STUB_H
#define CUPFILLER_ARDUINO_STUB_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string>

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

// ===========================================================================
//  TRẠNG THÁI MÁY ẢO
// ===========================================================================
struct StubMic {
  int amp = 0;                 // 0 = im lặng
  uint32_t period_us = 2778;    // ~360 Hz
  int n_bursts = 0;             // số "tiếng" sẽ phát
  uint32_t burst_us = 300000, gap_us = 250000;
  uint32_t t0_us = 0;
  uint32_t total_us = 0;
};

struct StubState {
  uint32_t us = 0;                       // đồng hồ ảo (micro giây)
  int pin[64];                           // mức từng chân
  int analog = 2048;                     // giá trị ADC mặc định (im lặng)
  float distance_mm = 150.0f;            // khoảng cách tới HC-SR04 (150 = khay trống)
  bool echo_ok = true;                   // false = không có tín hiệu dội về
  std::string tx;                        // bytes firmware đã gửi ra UART2 (kênh mặc định)
  std::string rx;                        // bytes PC gửi xuống UART2, firmware sẽ đọc dần
  std::string usb_tx;                    // bytes gửi ra CỔNG USB của board (UART_USE_USB_SERIAL)
  std::string usb_rx;                    // bytes PC gửi xuống qua cổng USB
  StubMic mic;
  int relay_toggles = 0;
  // thống kê
  uint32_t n_pulsein = 0, n_analog = 0;
};

inline StubState &stubState() { static StubState s; return s; }

inline void stub_reset() {
  StubState &s = stubState();
  s = StubState();
  for (int i = 0; i < 64; i++) s.pin[i] = HIGH;      // như điện trở kéo lên nội
}

// ---- đồng hồ ảo -----------------------------------------------------------
inline uint32_t stub_now_us() { return stubState().us; }
inline void stub_advance_us(uint32_t d) { stubState().us += d; }
inline void stub_advance_ms(uint32_t d) { stubState().us += d * 1000u; }

// ---- chân GPIO ------------------------------------------------------------
inline void stub_set_pin(int p, int level) {
  if (p >= 0 && p < 64) stubState().pin[p] = level;
}
inline int stub_pin(int p) { return (p >= 0 && p < 64) ? stubState().pin[p] : LOW; }

// ---- cảm biến siêu âm / analog -------------------------------------------
inline void stub_set_distance_mm(float mm) { stubState().distance_mm = mm; }
inline float stub_distance_mm() { return stubState().distance_mm; }
inline void stub_set_echo_ok(bool ok) { stubState().echo_ok = ok; }
inline void stub_set_analog(int v) { stubState().analog = v; }

// ---- mic: phát N "tiếng" (burst) để thử VAD/đếm tiếng ---------------------
inline void stub_mic_bursts(int n, uint32_t burst_us = 300000, uint32_t gap_us = 250000,
                            int amp = 1200) {
  StubMic &m = stubState().mic;
  m.n_bursts = n;
  m.burst_us = burst_us;
  m.gap_us = gap_us;
  m.amp = amp;
  m.t0_us = stubState().us;
  m.total_us = (uint32_t)n * burst_us + (uint32_t)(n > 0 ? n - 1 : 0) * gap_us;
}
inline int stub_mic_amp_now() {
  StubMic &m = stubState().mic;
  if (m.amp == 0 || m.n_bursts <= 0) return 0;
  uint32_t t = stubState().us - m.t0_us;
  if (t >= m.total_us) return 0;
  uint32_t period = m.burst_us + m.gap_us;
  uint32_t phase = t % period;
  return (phase < m.burst_us) ? m.amp : 0;
}

// ---- UART (nối với PC) ----------------------------------------------------
inline void stub_uart_tx(const uint8_t *p, size_t n) {
  if (p && n) stubState().tx.append((const char *)p, n);
}
inline void stub_uart_inject(const uint8_t *p, size_t n) {
  if (p && n) stubState().rx.append((const char *)p, n);
}
inline void stub_uart_inject(const std::string &s) {
  stub_uart_inject((const uint8_t *)s.data(), s.size());
}
inline std::string stub_uart_take() {
  std::string out;
  out.swap(stubState().tx);
  return out;
}
inline void stub_uart_inject_usb(const std::string &s) {
  stubState().usb_rx.append(s);
}
inline std::string stub_usb_take() {
  std::string out;
  out.swap(stubState().usb_tx);
  return out;
}

// ===========================================================================
//  API KIỂU ARDUINO
// ===========================================================================
inline uint32_t millis() { return stubState().us / 1000u; }
inline uint32_t micros() { return stubState().us; }
inline void delay(uint32_t ms) { stubState().us += ms * 1000u; }
inline void delayMicroseconds(uint32_t us) { stubState().us += us; }

inline void pinMode(int p, int mode) {
  if (p < 0 || p >= 64) return;
  if (mode == INPUT_PULLUP && stubState().pin[p] != LOW) stubState().pin[p] = HIGH;
  (void)mode;
}
inline int digitalRead(int p) { return stub_pin(p); }
inline void digitalWrite(int p, int level) {
  if (p >= 0 && p < 64 && stubState().pin[p] != level) stubState().relay_toggles++;
  stub_set_pin(p, level);
}
inline int analogRead(int p) {
  (void)p;
  stubState().n_analog++;
  // ADC mất ~62 us mỗi mẫu -> vòng đo tần số của firmware (voice_mic.h) đo được
  // ~16.12 kHz, đúng bằng MIC_SAMPLE_RATE trong config.h. Nhờ vậy PCM mà firmware
  // gửi lên PC có mốc thời gian KHỚP với thời gian thật (nếu ADC "miễn phí" thì PCM
  // bị nén lại, PC đếm "tiếng" sai vì khoảng lặng ngắn hơn thực tế).
  stubState().us += 62;
  int tone = stub_mic_amp_now();
  if (tone == 0) return stubState().analog;
  double t = (double)stub_now_us() * 1e-6;
  double f = 1e6 / (double)stubState().mic.period_us;
  return stubState().analog + (int)(tone * sin(2.0 * 3.14159265358979 * f * t));
}
inline void analogReadResolution(int) {}
// HC-SR04: độ dài xung echo ~ 58 us cho mỗi cm (âm thanh đi và về)
inline uint32_t pulseIn(int pin, int state, uint32_t timeout = 1000000UL) {
  (void)pin; (void)state;
  StubState &s = stubState();
  s.n_pulsein++;
  s.us += 10;                            // thời gian bắn xung
  if (!s.echo_ok || s.distance_mm <= 0.0f || s.distance_mm > 4000.0f) return 0;
  uint32_t d = (uint32_t)(s.distance_mm * 2000.0f / 343.0f);   // us
  if (d > timeout) return 0;
  s.us += d;                             // chờ echo
  return d;
}

// ---- Serial giả (để sketch gọi print/println thoải mái) --------------------
// ---- UART nối PC: ghi ra được thu lại, đọc vào lấy từ đệm PC gửi xuống ----
// Cổng UART giả: giống core ESP32, mỗi cổng có đệm RX/TX RIÊNG.
//   uart_num = 0 -> cổng USB của board (Serial)      -> đệm usb_*
//   uart_num = 2 -> UART2 trên GPIO16/17 (Serial2)   -> đệm tx/rx (kênh mặc định)
class HardwareSerial {
 public:
  explicit HardwareSerial(int uart_num = 2) : _usb(uart_num == 0) {}
  void begin(unsigned long, uint32_t = 0, int = -1, int = -1) {}
  void setRxBufferSize(size_t) {}
  void setTxBufferSize(size_t) {}
  int available() { return (int)_rx().size(); }
  int availableForWrite() { return 256; }
  int read() {
    std::string &rx = _rx();
    if (rx.empty()) return -1;
    int b = (unsigned char)rx[0];
    rx.erase(0, 1);
    return b;
  }
  size_t write(const uint8_t *p, size_t n) {
    if (p && n) _tx().append((const char *)p, n);
    return n;
  }
  size_t write(uint8_t b) { _tx().push_back((char)b); return 1; }
  void flush() {}
  // Cổng USB bỏ qua log khi firmware chỉ in DEBUG_SERIAL (không phải kênh PC)
  bool isUsb() const { return _usb; }

 private:
  std::string &_rx() { return _usb ? stubState().usb_rx : stubState().rx; }
  std::string &_tx() { return _usb ? stubState().usb_tx : stubState().tx; }
  bool _usb;
};

// Log của firmware (Serial.print) đi vào đệm riêng để không lẫn vào gói tin
class StubSerial : public HardwareSerial {
 public:
  StubSerial() : HardwareSerial(0) {}
  template <class T> void print(T) {}
  template <class T> void print(T, int) {}
  void print(const char *) {}
  void print(const char *, int) {}
  template <class T> void println(T) {}
  template <class T> void println(T, int) {}
  void println() {}
  void println(const char *) {}
};

extern StubSerial Serial;
extern HardwareSerial Serial2;

#endif  // CUPFILLER_ARDUINO_STUB_H
