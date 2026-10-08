// Kenh UART voi may tinh tren ESP32 that: dung cong Serial (USB-UART).
#if defined(ARDUINO)

#include <Arduino.h>

#include "config.h"
#include "link_io.h"

namespace cupfiller {

void link_write(const uint8_t *data, size_t n) { Serial.write(data, (size_t)n); }

size_t link_read(uint8_t *buf, size_t max_n) {
  size_t k = 0;
  while (k < max_n) {
    const int c = Serial.read();
    if (c < 0) break;
    buf[k++] = (uint8_t)c;
  }
  return k;
}

}  // namespace cupfiller

#endif  // ARDUINO
