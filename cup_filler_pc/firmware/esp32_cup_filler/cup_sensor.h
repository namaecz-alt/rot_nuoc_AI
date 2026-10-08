// ===========================================================================
//  cup_sensor.h - PHÁT HIỆN CỐC ĐẶT VÀO KHAY
//
//  Loại 0 (mặc định): HC-SR04 siêu âm treo phía trên khay, đo khoảng cách tới
//  mặt khay. Khi có cốc, khoảng cách giảm đi đúng bằng chiều cao cốc:
//        chiều cao cốc = baseline (khay trống) - khoảng cách hiện tại
//  Loại 1: cảm biến hồng ngoại phản xạ (digital, tích cực mức thấp).
//  Loại 2: công tắc hành trình (digital, tích cực mức thấp).
//
//  Lọc: trung vị CUP_MEDIAN_N mẫu + chống dội theo thời gian (CUP_DEBOUNCE_MS để
//  báo "đã đặt cốc", CUP_MISSING_MS để báo "đã nhấc cốc").
// ===========================================================================
#ifndef CUPFILLER_CUP_SENSOR_H
#define CUPFILLER_CUP_SENSOR_H

#include <Arduino.h>
#include "config.h"

class CupSensor {
 public:
  void begin(float baselineMm = CUP_BASELINE_MM) {
    _baseline = baselineMm;
    _samples = 0;
    _present = false;
    _rawPresent = false;
    _tLastSample = 0;
    _tLastChange = 0;
    _evtPending = false;
    _evtPresent = false;
#if CUP_SENSOR_TYPE == 0
    pinMode(PIN_TRIG, OUTPUT);
    pinMode(PIN_ECHO, INPUT);
    digitalWrite(PIN_TRIG, LOW);
#else
    pinMode(PIN_CUP_DIGITAL, INPUT_PULLUP);   // cảm biến/công tắc kéo về GND khi có cốc
#endif
    tare(millis());                            // đo baseline lúc khay trống
  }

  // Đo lại mặt khay (yêu cầu khay TRỐNG). Trả về baseline mới (mm).
  float tare(uint32_t nowMs) {
#if CUP_SENSOR_TYPE == 0
    float acc = 0.0f;
    uint8_t n = 0;
    for (uint8_t i = 0; i < 5; i++) {
      float d = _measureOnce();
      if (d > 10.0f) {
        acc += d;
        n++;
      }
      delay(20);
    }
    if (n >= 3) _baseline = acc / (float)n;
#endif
    _tLastSample = nowMs;
    _samples = 0;
    return _baseline;
  }

  void setBaseline(float mm) { if (mm > 10.0f) _baseline = mm; }
  float baseline() const { return _baseline; }
  float lastDistance() const { return _distance; }
  float lastHeight() const { return _height; }

  // Chiều cao cốc đo được (mm) - 0 nếu đang trống
  float cupHeight() const { return _height; }

  bool present() const { return _present; }
  bool valid() const { return _distance > 5.0f; }

  // Lấy sự kiện thay đổi trạng thái (đặt vào / nhấc ra). true nếu có sự kiện.
  bool takeEvent(bool &nowPresent) {
    if (!_evtPending) return false;
    _evtPending = false;
    nowPresent = _evtPresent;
    return true;
  }

  // Gọi đều đặn trong loop()
  void update(uint32_t nowMs) {
#if CUP_SENSOR_TYPE == 0
    if ((nowMs - _tLastSample) >= CUP_SAMPLE_MS) {
      _tLastSample = nowMs;
      float d = _measureOnce();
      if (d > 5.0f && d <= CUP_MAX_DISTANCE_MM) {
        _median[_samples % CUP_MEDIAN_N] = d;
        if (_samples < CUP_MEDIAN_N) _samples++;
        _distance = _medianOf(_samples);
        _height = _baseline - _distance;
        _rawPresent = (_height >= CUP_MIN_HEIGHT_MM);
        _valid = true;
      } else {
        // không đọc được / quá xa: coi như khay trống nhưng KHÔNG xoá baseline
        _valid = false;
        _rawPresent = false;
        _height = 0.0f;
      }
    }
#else
    _rawPresent = (digitalRead(PIN_CUP_DIGITAL) == LOW);
    _height = _rawPresent ? 80.0f : 0.0f;      // cảm biến số không đo được chiều cao
    _valid = true;
#endif
    // chống dội: trạng thái phải giữ nguyên đủ lâu mới được công nhận
    uint32_t need = _rawPresent ? CUP_DEBOUNCE_MS : CUP_MISSING_MS;
    if (_rawPresent != _present) {
      if ((nowMs - _tLastChange) >= need) {
        _present = _rawPresent;
        _tLastChange = nowMs;
        _evtPending = true;
        _evtPresent = _present;
      }
    } else {
      _tLastChange = nowMs;
    }
  }

 private:
  float _measureOnce() {
#if CUP_SENSOR_TYPE == 0
    digitalWrite(PIN_TRIG, LOW);
    delayMicroseconds(4);
    digitalWrite(PIN_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_TRIG, LOW);
    uint32_t dur = pulseIn(PIN_ECHO, HIGH, 30000UL);   // µs (30 ms ~ 5 m)
    if (dur == 0) return 0.0f;
    return (float)dur * 0.343f / 2.0f;                 // mm (âm thanh 343 m/s)
#else
    return digitalRead(PIN_CUP_DIGITAL) == LOW ? 70.0f : _baseline;
#endif
  }

  float _medianOf(uint8_t n) {
    float tmp[CUP_MEDIAN_N];
    for (uint8_t i = 0; i < n; i++) tmp[i] = _median[i];
    for (uint8_t i = 1; i < n; i++) {                  // sắp xếp chèn (n nhỏ)
      float key = tmp[i];
      int8_t j = (int8_t)i - 1;
      while (j >= 0 && tmp[j] > key) {
        tmp[j + 1] = tmp[j];
        j--;
      }
      tmp[j + 1] = key;
    }
    return tmp[n / 2];
  }

  float _median[CUP_MEDIAN_N] = {0, 0, 0};
  uint8_t _samples = 0;
  float _baseline = CUP_BASELINE_MM;
  float _distance = 0.0f;
  float _height = 0.0f;
  bool _valid = false;
  bool _rawPresent = false;
  bool _present = false;
  bool _evtPending = false;
  bool _evtPresent = false;
  uint32_t _tLastSample = 0;
  uint32_t _tLastChange = 0;
};

#endif  // CUPFILLER_CUP_SENSOR_H
