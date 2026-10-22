// Wire.h gia cho host-test cua esp32_pi_link.
// Chi can du API de lcd_2004_i2c.h bien dich va chay logic tren may tinh:
// ACK dia chi 0x27, cac ghi I2C duoc chap nhan nhung khong di dau ca.
#ifndef CUPFILLER_PI_WIRE_STUB_H
#define CUPFILLER_PI_WIRE_STUB_H

#include <stddef.h>
#include <stdint.h>

class TwoWire {
 public:
  void begin(int, int, uint32_t) {}
  void beginTransmission(uint8_t address) { _address = address; }
  size_t write(const uint8_t *, size_t len) { return len; }
  uint8_t endTransmission() { return _address == 0x27 ? 0 : 2; }

 private:
  uint8_t _address = 0;
};

// Header-only stub cho mot translation unit cua host-test.
static TwoWire Wire;

#endif  // CUPFILLER_PI_WIRE_STUB_H
