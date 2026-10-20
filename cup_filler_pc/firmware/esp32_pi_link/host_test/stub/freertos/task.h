// Stub host-test: không tạo task thật; audio chỉ được kiểm cú pháp.
#pragma once
#include "FreeRTOS.h"

typedef void *TaskHandle_t;
typedef void (*TaskFunction_t)(void *);

inline BaseType_t xTaskCreatePinnedToCore(TaskFunction_t, const char *, uint32_t,
                                          void *, UBaseType_t,
                                          TaskHandle_t *task, BaseType_t) {
  if (task) *task = (TaskHandle_t)0x1;
  return pdPASS;
}
