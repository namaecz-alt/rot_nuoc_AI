// Kenh truyen UART cua giao thuc (chi 2 ham) -> tach khoi Arduino de test tren PC.
//   - Tren ESP32 : link_io_arduino.cpp  (Serial / USB-CDC)
//   - Tren PC    : host/link_io_host.cpp (stdin/stdout, dung cho simulator + unit test)
#ifndef CUPFILLER_LINK_IO_H
#define CUPFILLER_LINK_IO_H

#include <stddef.h>
#include <stdint.h>

namespace cupfiller {

void link_write(const uint8_t *data, size_t n);

// Doc toi da max_n byte da co san; tra ve so byte doc duoc (0 = chua co gi).
size_t link_read(uint8_t *buf, size_t max_n);

}  // namespace cupfiller

#endif  // CUPFILLER_LINK_IO_H
