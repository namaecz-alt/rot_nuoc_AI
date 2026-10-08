// Lop che GPIO cho ESP32 that (Arduino core).
// File nay chi bien dich khi build bang Arduino IDE / PlatformIO.
#if defined(ARDUINO)

#include <Arduino.h>

#include "board_io.h"

namespace cupfiller {

uint32_t io_millis() { return millis(); }

void io_pin_input_pullup(uint8_t pin) {
  // NUT BAM & CAM BIEN: tich cuc muc THAP -> bat buoc INPUT_PULLUP.
  pinMode(pin, INPUT_PULLUP);
}

void io_pin_output(uint8_t pin) {
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);  // relay tich cuc CAO -> LOW la TAT
}

int io_read(uint8_t pin) { return digitalRead(pin) == HIGH ? 1 : 0; }

void io_write(uint8_t pin, int level) { digitalWrite(pin, level ? HIGH : LOW); }

void io_delay_ms(uint32_t ms) { delay(ms); }

}  // namespace cupfiller

#endif  // ARDUINO
