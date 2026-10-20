// Stub tối thiểu chỉ để test_pi_run.cpp biên dịch sketch trên máy tính.
// Firmware thật dùng header FreeRTOS của ESP-IDF, không dùng file này.
#pragma once
#include <stdint.h>

typedef long BaseType_t;
typedef unsigned long UBaseType_t;
typedef uint32_t TickType_t;

#ifndef pdPASS
#define pdPASS (1)
#endif
#ifndef pdFAIL
#define pdFAIL (0)
#endif
#ifndef portMAX_DELAY
#define portMAX_DELAY ((TickType_t)0xffffffffUL)
#endif

struct portMUX_TYPE { int dummy; };
#define portMUX_INITIALIZER_UNLOCKED {0}
#define portENTER_CRITICAL(m) do { (void)(m); } while (0)
#define portEXIT_CRITICAL(m) do { (void)(m); } while (0)
