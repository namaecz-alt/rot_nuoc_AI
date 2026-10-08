// Lop che phan cung toi thieu: GPIO + thoi gian.
//   - Tren ESP32 : board_io_arduino.cpp
//   - Tren PC    : host/board_io_host.cpp (thoi gian ao + chan ao, cho unit test)
#ifndef CUPFILLER_BOARD_IO_H
#define CUPFILLER_BOARD_IO_H

#include <stdint.h>

namespace cupfiller {

uint32_t io_millis();

void io_pin_input_pullup(uint8_t pin);  // INPUT_PULLUP (nut bam tich cuc muc THAP)
void io_pin_output(uint8_t pin);        // relay / LED
int io_read(uint8_t pin);               // 0 hoac 1
void io_write(uint8_t pin, int level);  // 0 hoac 1

// Tre khong chan (dung cho beep ngan luc khoi dong)
void io_delay_ms(uint32_t ms);

}  // namespace cupfiller

#endif  // CUPFILLER_BOARD_IO_H
