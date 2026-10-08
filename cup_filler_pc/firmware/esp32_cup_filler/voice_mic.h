// ===========================================================================
//  voice_mic.h - MIC + PHÁT HIỆN TIẾNG NÓI (để chọn mức nước bằng giọng nói)
//
//  Hoạt động:
//    1. Đọc mẫu liên tục (block nhỏ mỗi vòng loop) ở tần số ~MIC_SAMPLE_RATE.
//    2. Tính RMS theo khung 10 ms -> VAD (phát hiện bắt đầu/kết thúc đoạn nói).
//    3. Trong lúc có tiếng:
//         VOICE_MODE = 2: đóng gói PCM 16-bit gửi lên PC (AUDIO_CHUNK, có cờ
//                         START/END + mã tần số lấy mẫu thực tế trong nibble cao)
//                         -> PC nhận dạng rồi gửi lệnh bơm xuống.
//         VOICE_MODE = 1: tự đếm số "tiếng" ngay trên ESP -> chọn preset + bơm,
//                         hoạt động cả khi không có PC.
//       Cả hai chế độ đều đếm số tiếng để gửi kèm VOICE_EVENT làm phương án dự phòng.
//
//  Hai loại mic:
//    MIC_TYPE = 1: ANALOG (MAX9814 / MAX4466) vào ADC - đơn giản, luôn biên dịch được.
//    MIC_TYPE = 2: I2S số (INMP441 / ICS-43434) - chất lượng tốt hơn cho nhận dạng
//                  chữ trên PC (cần đúng phiên bản arduino-esp32, xem README).
// ===========================================================================
#ifndef CUPFILLER_VOICE_MIC_H
#define CUPFILLER_VOICE_MIC_H

#include <Arduino.h>
#include "config.h"

#if MIC_TYPE == 2
  #if __has_include(<driver/i2s.h>)
    #include <driver/i2s.h>          // API I2S "legacy" (IDF4/IDF5 đều còn)
  #else
    #error "Khong tim thay driver/i2s.h. Hay doi MIC_TYPE 1 (mic analog MAX9814/MAX4466) hoac dung vi du ESP_I2S.h cua arduino-esp32 3.x."
  #endif
#endif

typedef void (*VoiceChunkCb)(const int16_t *samples, uint16_t n, uint8_t flags, void *user);
typedef void (*VoiceSegmentCb)(uint16_t nPeaks, uint16_t rms, uint16_t durMs, void *user);

class VoiceEngine {
 public:
  void setCallbacks(VoiceChunkCb chunkCb, VoiceSegmentCb segCb, void *user) {
    _chunkCb = chunkCb;
    _segCb = segCb;
    _user = user;
  }

  void begin() {
#if MIC_TYPE != 0
    _rateHz = MIC_SAMPLE_RATE;
  #if MIC_TYPE == 1
    pinMode(PIN_MIC_ADC, INPUT);
    analogReadResolution(12);              // 0..4095, giữa dải ~2048
    _rateHz = _measureAnalogRate();
  #elif MIC_TYPE == 2
    _beginI2S();
  #endif
#endif
    _active = false;
    _chunkFill = 0;
    _nPeaks = 0;
    _tSegmentStart = 0;
    _tLastLoud = 0;
    _loudFrames = 0;
    _inBurst = false;
    _tLastBurstEnd = 0;
  }

  bool enabled() const { return MIC_TYPE != 0; }
  uint32_t sampleRate() const { return _rateHz; }
  bool active() const { return _active; }
  uint16_t lastPeaks() const { return _nPeaks; }
  uint16_t lastRms() const { return _lastRms; }
  uint16_t lastDurationMs() const { return _lastDurMs; }

  // Gọi liên tục trong loop(): đọc & xử lý một khối mẫu
  void update(uint32_t nowMs) {
#if MIC_TYPE != 0
    int16_t block[MIC_BLOCK_SAMPLES];
    uint16_t n = _readBlock(block, MIC_BLOCK_SAMPLES);
    if (n == 0) return;
    _process(block, n, nowMs);
#else
    (void)nowMs;
#endif
  }

  // Gọi khi cần kết thúc đoạn nói ngay (ví dụ: người dùng nhấn nút)
  void flushSegment(uint32_t nowMs) {
    if (_active) _endSegment(nowMs);
  }

 private:
  // ================== ĐỌC MẪU ==================
  // MIC_TYPE=1: ADC, đo luôn tần số lấy mẫu thực tế (ADC ESP32 ~ 20-40 kSa/s)
  uint32_t _measureAnalogRate() {
    uint32_t t0 = micros();
    uint32_t n = 0;
    while ((micros() - t0) < 100000UL) {       // đo trong 100 ms
      analogRead(PIN_MIC_ADC);
      n++;
    }
    uint32_t rate = n * 10;                    // mẫu/giây
    if (rate < 4000) rate = 4000;
    // Mã tần số gửi PC chỉ có 4 bit (AU_RATE_SHIFT) -> tối đa 15 x 2000 = 30000 Hz.
    if (rate > (uint32_t)(15 * 2000)) rate = 15 * 2000;
    return rate;
  }

  uint16_t _readBlock(int16_t *out, uint16_t maxN) {
    uint16_t n = 0;
#if MIC_TYPE == 1
    uint32_t t0 = micros();
    for (n = 0; n < maxN; n++) {
      int raw = analogRead(PIN_MIC_ADC);       // 0..4095
      int32_t s = ((int32_t)raw - 2048) * MIC_GAIN_NUM / MIC_GAIN_DEN;
      if (s > 32767) s = 32767;
      if (s < -32768) s = -32768;
      out[n] = (int16_t)s;
    }
    (void)t0;
#elif MIC_TYPE == 2
    n = _readI2S(out, maxN);
#endif
    return n;
  }

  // ================== XỬ LÝ ==================
  void _process(const int16_t *block, uint16_t n, uint32_t nowMs) {
    uint16_t frameLen = (uint16_t)(_rateHz / 100);          // khung 10 ms
    if (frameLen < 16) frameLen = 16;
    uint32_t sum = 0;
    for (uint16_t i = 0; i < n; i++) sum += (uint32_t)((int32_t)block[i] * block[i] >> 8);
    uint16_t rms = (uint16_t)sqrtf((float)sum / (float)n * 256.0f);
    if (rms > _lastRms) _lastRms = rms;

    if (!_active) {
      // chờ BẮT ĐẦU đoạn nói: nhiều khung liên tiếp vượt ngưỡng
      if (rms >= VAD_ON_RMS) {
        _loudFrames++;
        if (_loudFrames >= VAD_START_FRAMES) {
          _startSegment(nowMs);
          _pushSamples(block, n, 0);
        }
      } else {
        _loudFrames = 0;
      }
      return;
    }

    // đang trong đoạn nói
    _pushSamples(block, n, 0);
    _countPeak(rms, nowMs);
    if (rms >= VAD_OFF_RMS) _tLastLoud = nowMs;
    if ((nowMs - _tLastLoud) >= VAD_END_MS) _endSegment(nowMs);
    else if ((nowMs - _tSegmentStart) >= VAD_MAX_SEGMENT_MS) _endSegment(nowMs);
  }

  void _startSegment(uint32_t nowMs) {
    _active = true;
    _tSegmentStart = nowMs;
    _tLastLoud = nowMs;
    _nPeaks = 0;
    _chunkFill = 0;
    _inBurst = false;
    _tLastBurstEnd = 0;
    _lastRms = 0;
    _startFlag = 1;
  }

  void _endSegment(uint32_t nowMs) {
    _active = false;
    _loudFrames = 0;
    _lastDurMs = (uint16_t)min((uint32_t)60000, nowMs - _tSegmentStart);
    if (_chunkFill > 0) _flushChunk(2);            // 2 = cờ END
    if (_segCb != NULL) _segCb(_nPeaks, _lastRms, _lastDurMs, _user);
  }

  // Đếm "tiếng" (âm tiết): mỗi lần RMS vượt ngưỡng ON, tối thiểu VAD_MIN_BURST_MS,
  // cách nhau tối thiểu VAD_MIN_GAP_MS (giống thuật toán PeakCounter bên PC).
  void _countPeak(uint16_t rms, uint32_t nowMs) {
    const uint32_t minBurst = 60, minGap = 120;
    if (!_inBurst) {
      if (rms >= VAD_ON_RMS) {
        if (_tLastBurstEnd == 0 || (nowMs - _tLastBurstEnd) >= minGap) {
          _nPeaks++;
          _inBurst = true;
          _tBurstStart = nowMs;
        } else {
          _inBurst = true;                          // gộp vào tiếng trước
          _tBurstStart = nowMs;
        }
      }
    } else {
      if (rms < VAD_OFF_RMS) {
        _inBurst = false;
        if ((nowMs - _tBurstStart) < minBurst) _nPeaks--;   // quá ngắn -> bỏ
        _tLastBurstEnd = nowMs;
      }
    }
  }

  // Đóng gói PCM: gom đủ MIC_CHUNK_SAMPLES rồi gọi callback (nếu VOICE_MODE=2)
  void _pushSamples(const int16_t *block, uint16_t n, uint8_t) {
#if VOICE_MODE == 2
    for (uint16_t i = 0; i < n; i++) {
      _chunk[_chunkFill++] = block[i];
      if (_chunkFill >= MIC_CHUNK_SAMPLES) _flushChunk(0);
    }
#else
    (void)block;
    (void)n;
#endif
  }

  void _flushChunk(uint8_t endFlag) {
    if (_chunkCb == NULL || _chunkFill == 0) {
      _chunkFill = 0;
      _startFlag = 0;
      return;
    }
    // nibble cao của flags = tần số lấy mẫu thực tế / 2 kHz (0 = dùng mặc định)
    uint8_t flags = (uint8_t)(_startFlag ? 1 : 0) | (uint8_t)((endFlag & 2) ? 2 : 0);
    uint32_t rc = _rateHz / 2000;                    // 4 bit: 0..15 (0 = PC dùng mặc định)
    if (rc > 15) rc = 15;
    uint8_t rateCode = (uint8_t)rc;
    flags |= (uint8_t)(rateCode << 4);
    _chunkCb(_chunk, _chunkFill, flags, _user);
    _chunkFill = 0;
    _startFlag = 0;
  }

  // ================== I2S (MIC_TYPE=2) ==================
#if MIC_TYPE == 2
  void _beginI2S() {
    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    cfg.sample_rate = MIC_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;   // INMP441 phát ở kênh trái
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 4;
    cfg.dma_buf_len = 128;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = false;
    cfg.fixed_mclk = 0;
    i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);
    i2s_pin_config_t pins = {};
    pins.bck_io_num = PIN_I2S_SCK;
    pins.ws_io_num = PIN_I2S_WS;
    pins.data_out_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num = PIN_I2S_SD;
    i2s_set_pin(I2S_NUM_0, &pins);
    i2s_zero_dma_buffer(I2S_NUM_0);
    i2s_start(I2S_NUM_0);
    _rateHz = MIC_SAMPLE_RATE;                       // I2S bám đúng tần số yêu cầu
  }

  uint16_t _readI2S(int16_t *out, uint16_t maxN) {
    size_t bytesRead = 0;
    esp_err_t err = i2s_read(I2S_NUM_0, (void *)out, (size_t)maxN * sizeof(int16_t),
                             &bytesRead, pdMS_TO_TICKS(20));
    if (err != ESP_OK) return 0;
    return (uint16_t)(bytesRead / sizeof(int16_t));
  }
#endif

  // ================== BIẾN ==================
  static const uint16_t MIC_BLOCK_SAMPLES = 32;
  int16_t _chunk[MIC_CHUNK_SAMPLES];
  uint16_t _chunkFill = 0;
  uint8_t _startFlag = 0;
  VoiceChunkCb _chunkCb = NULL;
  VoiceSegmentCb _segCb = NULL;
  void *_user = NULL;
  uint32_t _rateHz = MIC_SAMPLE_RATE;
  bool _active = false;
  uint16_t _nPeaks = 0;
  uint16_t _lastRms = 0;
  uint16_t _lastDurMs = 0;
  uint32_t _tSegmentStart = 0;
  uint32_t _tLastLoud = 0;
  uint32_t _tBurstStart = 0;
  uint32_t _tLastBurstEnd = 0;
  bool _inBurst = false;
  uint8_t _loudFrames = 0;
};

#endif  // CUPFILLER_VOICE_MIC_H
