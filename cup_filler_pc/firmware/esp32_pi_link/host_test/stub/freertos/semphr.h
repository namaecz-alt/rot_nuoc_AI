// Stub host-test cho mutex UART.
#pragma once
#include "FreeRTOS.h"

typedef void *SemaphoreHandle_t;

inline SemaphoreHandle_t xSemaphoreCreateMutex() {
  return (SemaphoreHandle_t)0x1;
}
inline BaseType_t xSemaphoreTake(SemaphoreHandle_t, TickType_t) { return pdPASS; }
inline BaseType_t xSemaphoreGive(SemaphoreHandle_t) { return pdPASS; }
