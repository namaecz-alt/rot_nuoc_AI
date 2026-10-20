// Stub I2S tối thiểu cho host-test; firmware thật dùng driver của ESP-IDF.
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef int i2s_port_t;
#define I2S_NUM_0 (0)
#ifndef I2S_PIN_NO_CHANGE
#define I2S_PIN_NO_CHANGE (-1)
#endif
#ifndef ESP_INTR_FLAG_LEVEL1
#define ESP_INTR_FLAG_LEVEL1 (1)
#endif

typedef enum { I2S_MODE_MASTER = 1, I2S_MODE_TX = 2, I2S_MODE_RX = 4 } i2s_mode_t;
typedef enum {
  I2S_BITS_PER_SAMPLE_16BIT = 16,
  I2S_BITS_PER_SAMPLE_32BIT = 32
} i2s_bits_per_sample_t;
typedef enum { I2S_CHANNEL_FMT_RIGHT_LEFT = 0 } i2s_channel_fmt_t;
typedef enum { I2S_COMM_FORMAT_STAND_I2S = 1 } i2s_comm_format_t;

typedef struct {
  i2s_mode_t mode;
  int sample_rate;
  i2s_bits_per_sample_t bits_per_sample;
  i2s_channel_fmt_t channel_format;
  i2s_comm_format_t communication_format;
  int intr_alloc_flags;
  int dma_buf_count;
  int dma_buf_len;
  bool use_apll;
  bool tx_desc_auto_clear;
  int fixed_mclk;
} i2s_config_t;

typedef struct {
  int mck_io_num;
  int bck_io_num;
  int ws_io_num;
  int data_out_num;
  int data_in_num;
} i2s_pin_config_t;

inline int i2s_driver_install(i2s_port_t, const i2s_config_t *, int, void *) { return 0; }
inline int i2s_set_pin(i2s_port_t, const i2s_pin_config_t *) { return 0; }
inline int i2s_zero_dma_buffer(i2s_port_t) { return 0; }
inline int i2s_start(i2s_port_t) { return 0; }
inline int i2s_write(i2s_port_t, const void *, size_t n, size_t *written, uint32_t) {
  if (written) *written = n;
  return 0;
}
inline int i2s_read(i2s_port_t, void *, size_t, size_t *read, uint32_t) {
  if (read) *read = 0;
  return 0;
}
