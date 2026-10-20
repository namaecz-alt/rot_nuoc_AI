// ===========================================================================
//  audio_duplex.h - I2S SONG CÔNG: INMP441 (mic) + MAX98357A (loa)
//
//  Dùng chung một I2S peripheral của ESP32:
//      MASTER | TX | RX, 16000 Hz, slot 32-bit stereo
//      BCLK=GPIO18, LRCLK=GPIO19
//      data-in  = GPIO23 (INMP441 SD)
//      data-out = PIN_I2S_SPK_DIN trong pi_config.h (mặc định GPIO13)
//
//  * Task audio chạy riêng ở core 0; loop() chính không bị i2s_read/write chặn.
//  * Mic -> callback ra từng AUDIO_CHUNK 96 mẫu int16 mono cho PiLink gửi Pi.
//    Mặc định chỉ gửi đoạn có tiếng (VAD); Pi có thể bật stream liên tục bằng
//    AUDIO_CTRL.mic_continuous=1.
//  * Loa <- Pi gửi SPEAKER_AUDIO PCM mono int16; ESP đệm vòng ~512 ms rồi nhân
//    đôi sang L/R. MAX98357A có SD=3V3 (kênh trái) vẫn nghe vì hai kênh bằng nhau.
//  * Khi loa đang phát, mặc định tạm không gửi mic để tránh tự thu tiếng máy.
// ===========================================================================
#ifndef CUPFILLER_PI_AUDIO_DUPLEX_H
#define CUPFILLER_PI_AUDIO_DUPLEX_H

#include <Arduino.h>
#include <driver/i2s.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <math.h>

#include "pi_config.h"
#include "pi_protocol.h"       // chỉ để lấy AU_START/AU_END của giao thức cũ

class AudioDuplex {
 public:
  typedef void (*MicChunkCb)(const int16_t *samples, uint16_t n,
                             uint8_t flags, void *user);

  void setMicCallback(MicChunkCb cb, void *user) {
    _micCb = cb;
    _micUser = user;
  }

  bool begin() {
    _ringHead = _ringTail = 0;

    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
    cfg.sample_rate = AUDIO_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 8;
    cfg.dma_buf_len = 256;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = true;             // thiếu dữ liệu TX -> im lặng
    cfg.fixed_mclk = 0;

    esp_err_t err = i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
    if (err != ESP_OK) {
#if DEBUG_SERIAL
      Serial.print("[I2S] driver_install loi: ");
      Serial.println((int)err);
#endif
      return false;
    }

    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE;
    pins.bck_io_num = PIN_I2S_BCLK;
    pins.ws_io_num = PIN_I2S_LRCLK;
    pins.data_out_num = PIN_I2S_SPK_DIN;
    pins.data_in_num = PIN_I2S_MIC_SD;
    err = i2s_set_pin(I2S_NUM_0, &pins);
    if (err != ESP_OK) {
#if DEBUG_SERIAL
      Serial.print("[I2S] set_pin loi: ");
      Serial.println((int)err);
#endif
      return false;
    }

    i2s_zero_dma_buffer(I2S_NUM_0);
    i2s_start(I2S_NUM_0);
    _ok = true;

    BaseType_t r = xTaskCreatePinnedToCore(
        _taskTrampoline, "pi_audio", 6144, this, 5, &_task, 0);
    if (r != pdPASS) {
      _ok = false;
      return false;
    }
    return true;
  }

  bool ok() const { return _ok; }
  bool micActive() const { return _segmentActive; }
  bool speakerActive() const {
    return (int32_t)(millis() - _speakerHotUntilMs) < 0;
  }
  uint32_t droppedSpeakerSamples() const { return _droppedSpeaker; }

  void setMicEnabled(bool en) { _micEnabled = en; }
  void setMicContinuous(bool en) { _micContinuous = en; }
  void setSpeakerEnabled(bool en) {
    _speakerEnabled = en;
    if (!en) {
      portENTER_CRITICAL(&_ringMux);
      _ringHead = _ringTail;
      portEXIT_CRITICAL(&_ringMux);
      _beepRemaining = 0;
    }
  }
  void setVolumePct(uint8_t pct) {
    _volumePct = (pct > 100) ? 100 : pct;
  }

  // Nhận PCM mono int16 từ Pi (hàm gọi ở loop chính sau khi giải SPEAKER_AUDIO).
  void pushSpeaker(const int16_t *samples, uint16_t n) {
    if (!_speakerEnabled || samples == NULL || n == 0) return;
    portENTER_CRITICAL(&_ringMux);
    for (uint16_t i = 0; i < n; i++) {
      uint16_t next = (uint16_t)((_ringHead + 1) & (SPEAKER_RING_SAMPLES - 1));
      if (next == _ringTail) {
        _droppedSpeaker += (uint32_t)(n - i);
        break;                                 // đệm đầy: bỏ phần mới, không chặn UART
      }
      _ring[_ringHead] = samples[i];
      _ringHead = next;
    }
    portEXIT_CRITICAL(&_ringMux);
    _speakerHotUntilMs = millis() + 250;
  }

  // Tiếng bíp nội bộ để test loa / báo nút; phát khi hàng đợi Pi đang trống.
  void beep(uint16_t freqHz, uint16_t durationMs) {
    if (!_speakerEnabled || freqHz == 0 || durationMs == 0) return;
    _beepFreq = freqHz;
    _beepRemaining = (uint32_t)AUDIO_SAMPLE_RATE * durationMs / 1000u;
    _speakerHotUntilMs = millis() + durationMs + 150;
  }

 private:
  static void _taskTrampoline(void *arg) {
    ((AudioDuplex *)arg)->_taskLoop();
  }

  void _taskLoop() {
    int32_t txStereo[AUDIO_BLOCK_FRAMES * 2];
    int32_t rxStereo[AUDIO_BLOCK_FRAMES * 2];
    int16_t mono[AUDIO_BLOCK_FRAMES];

    for (;;) {
      // 1) Chuẩn bị 10 ms cho loa (mẫu Pi, bíp, hoặc im lặng), L = R.
      for (uint16_t i = 0; i < AUDIO_BLOCK_FRAMES; i++) {
        int32_t s32 = ((int32_t)_nextSpeakerSample() << 16);
        txStereo[2 * i] = s32;
        txStereo[2 * i + 1] = s32;
      }
      size_t written = 0;
      i2s_write(I2S_NUM_0, txStereo, sizeof(txStereo), &written,
                pdMS_TO_TICKS(30));

      // 2) Đọc 10 ms từ INMP441 và lấy kênh đã cấu hình.
      size_t readBytes = 0;
      esp_err_t err = i2s_read(I2S_NUM_0, rxStereo, sizeof(rxStereo),
                               &readBytes, pdMS_TO_TICKS(30));
      if (err != ESP_OK || readBytes < sizeof(int32_t) * 2) continue;

      uint16_t frames = (uint16_t)(readBytes / (sizeof(int32_t) * 2));
      const uint8_t ch = MIC_CHANNEL_LEFT ? 0 : 1;
      for (uint16_t i = 0; i < frames; i++) {
        int32_t v = rxStereo[2 * i + ch] >> MIC_SHIFT;
        if (v > 32767) v = 32767;
        if (v < -32768) v = -32768;
        mono[i] = (int16_t)v;
      }
      _processMic(mono, frames);
    }
  }

  bool _popSpeaker(int16_t &s) {
    bool have = false;
    portENTER_CRITICAL(&_ringMux);
    if (_ringTail != _ringHead) {
      s = _ring[_ringTail];
      _ringTail = (uint16_t)((_ringTail + 1) & (SPEAKER_RING_SAMPLES - 1));
      have = true;
    }
    portEXIT_CRITICAL(&_ringMux);
    return have;
  }

  int16_t _nextSpeakerSample() {
    if (!_speakerEnabled) return 0;

    int32_t v = 0;
    int16_t queued = 0;
    if (_popSpeaker(queued)) {
      v = queued;
      _speakerHotUntilMs = millis() + 250;
    } else if (_beepRemaining > 0) {
      _beepRemaining--;
      _beepPhase += 2.0f * 3.141592653589793f * (float)_beepFreq /
                    (float)AUDIO_SAMPLE_RATE;
      if (_beepPhase > 2.0f * 3.141592653589793f) {
        _beepPhase -= 2.0f * 3.141592653589793f;
      }
      v = (int32_t)(sinf(_beepPhase) * 12000.0f);
      _speakerHotUntilMs = millis() + 250;
    }

    v = v * (int32_t)_volumePct / 100;
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    return (int16_t)v;
  }

  uint16_t _rms(const int16_t *s, uint16_t n) {
    if (n == 0) return 0;
    uint64_t acc = 0;
    for (uint16_t i = 0; i < n; i++) {
      int32_t v = s[i];
      acc += (uint64_t)(v * v);
    }
    float r = sqrtf((float)acc / (float)n);
    return (r > 65535.0f) ? 65535 : (uint16_t)r;
  }

  void _processMic(const int16_t *samples, uint16_t n) {
    if (!_micEnabled) {
      if (_segmentActive) _endSegment();
      _loudBlocks = 0;
      return;
    }

#if MUTE_MIC_WHILE_SPEAKER
    if (speakerActive()) {
      if (_segmentActive) _endSegment();
      _loudBlocks = 0;
      return;                                  // tránh Pi nghe lại tiếng loa
    }
#endif

    if (_micContinuous) {
      if (!_segmentActive) _startSegment();
      _pushMic(samples, n);
      return;
    }

    uint16_t rms = _rms(samples, n);
    if (!_segmentActive) {
      if (rms >= MIC_VAD_ON_RMS) {
        _loudBlocks++;
        if (_loudBlocks >= MIC_VAD_START_BLOCKS) {
          _startSegment();
          _pushMic(samples, n);
        }
      } else {
        _loudBlocks = 0;
      }
      return;
    }

    _pushMic(samples, n);
    _segmentBlocks++;
    if (rms >= MIC_VAD_OFF_RMS) _silentBlocks = 0;
    else _silentBlocks++;

    if (_silentBlocks >= MIC_VAD_END_BLOCKS ||
        _segmentBlocks >= MIC_MAX_SEGMENT_BLOCKS) {
      _endSegment();
    }
  }

  void _startSegment() {
    _segmentActive = true;
    _segmentStartPending = true;
    _segmentBlocks = 0;
    _silentBlocks = 0;
    _loudBlocks = 0;
    _chunkFill = 0;
  }

  void _pushMic(const int16_t *samples, uint16_t n) {
    for (uint16_t i = 0; i < n; i++) {
      _chunk[_chunkFill++] = samples[i];
      if (_chunkFill >= AUDIO_CHUNK_SAMPLES) _flushMic(false);
    }
  }

  void _flushMic(bool end) {
    if (_micCb == NULL) {
      _chunkFill = 0;
      _segmentStartPending = false;
      return;
    }
    uint8_t flags = (uint8_t)((8u << proto::AU_RATE_SHIFT) & 0xF0u); // 16 kHz
    if (_segmentStartPending) flags |= proto::AU_START;
    if (end) flags |= proto::AU_END;
    _micCb(_chunk, _chunkFill, flags, _micUser);
    _chunkFill = 0;
    _segmentStartPending = false;
  }

  void _endSegment() {
    if (_chunkFill > 0) {
      _flushMic(true);
    } else if (_micCb != NULL) {
      // Gói rỗng chỉ để báo END; nếu thiếu, phía Pi không biết đoạn nói đã hết.
      uint8_t flags = (uint8_t)(((8u << proto::AU_RATE_SHIFT) & 0xF0u) |
                                proto::AU_END);
      _micCb(_chunk, 0, flags, _micUser);
      _segmentStartPending = false;
    }
    _segmentActive = false;
    _segmentBlocks = 0;
    _silentBlocks = 0;
    _loudBlocks = 0;
  }

  MicChunkCb _micCb = NULL;
  void *_micUser = NULL;
  TaskHandle_t _task = NULL;
  bool _ok = false;

  volatile bool _micEnabled = true;
  volatile bool _micContinuous = false;
  volatile bool _speakerEnabled = true;
  volatile uint8_t _volumePct = SPEAKER_VOLUME_PCT;
  volatile uint32_t _speakerHotUntilMs = 0;
  volatile uint32_t _droppedSpeaker = 0;

  // Đệm vòng loa: kích thước phải là lũy thừa 2 (8192 trong pi_config.h).
  int16_t _ring[SPEAKER_RING_SAMPLES];
  volatile uint16_t _ringHead = 0;
  volatile uint16_t _ringTail = 0;
  portMUX_TYPE _ringMux = portMUX_INITIALIZER_UNLOCKED;

  volatile uint32_t _beepRemaining = 0;
  volatile uint16_t _beepFreq = 1000;
  float _beepPhase = 0.0f;

  int16_t _chunk[AUDIO_CHUNK_SAMPLES];
  uint16_t _chunkFill = 0;
  bool _segmentActive = false;
  bool _segmentStartPending = false;
  uint8_t _loudBlocks = 0;
  uint16_t _silentBlocks = 0;
  uint16_t _segmentBlocks = 0;
};

#endif  // CUPFILLER_PI_AUDIO_DUPLEX_H
