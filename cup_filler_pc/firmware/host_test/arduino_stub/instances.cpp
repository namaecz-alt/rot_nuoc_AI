#include "Arduino.h"
StubSerial Serial;          // cổng USB của board (log, hoặc kênh PC nếu UART_USE_USB_SERIAL=1)
HardwareSerial Serial2(2);  // UART2 trên GPIO16/17
