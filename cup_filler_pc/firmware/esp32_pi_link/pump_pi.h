// ===========================================================================
//  pump_pi.h - RELAY BƠM GPIO27, TÍCH CỰC MỨC CAO + AN TOÀN TẠI ESP32
//
//  Pi là bộ não vòng kín, nhưng ESP32 vẫn tự ngắt relay khi:
//    * ước lượng vượt MAX_FILL_ML / max_ml Pi gửi trong CUP_OK
//    * chạy quá POUR_TIMEOUT_MS
//    * mất cốc / mất UART (do esp32_pi_link.ino gọi forceOff)
//
//  Chế độ 0: ESP tự đong theo FLOW_ML_PER_S.
//  Chế độ 1: Pi điều khiển bằng PUMP_SET duty; relay cơ được băm chậm 2 giây.
// ===========================================================================
#ifndef CUPFILLER_PI_PUMP_H
#define CUPFILLER_PI_PUMP_H

#include <Arduino.h>
#include "pi_config.h"

class PiPump {
 public:
  enum Result : uint8_t {
    RUNNING = 0,
    DONE_TARGET = 1,
    STOP_OVER_VOLUME = 2,
    STOP_TIMEOUT = 3,
    IDLE = 255,
  };

  void begin() {
    pinMode(PIN_RELAY, OUTPUT);
    _setRelay(false);                         // an toàn ngay khi khởi động
    _flowMlS = FLOW_ML_PER_S;
    _maxMl = MAX_FILL_ML;
    _timeoutMs = POUR_TIMEOUT_MS;
    _finished = true;
    _result = IDLE;
  }

  void setFlow(float mlPerS) { if (mlPerS > 0.1f) _flowMlS = mlPerS; }
  float flow() const { return _flowMlS; }
  void setMaxMl(float ml) {
    if (ml > 0.0f) _maxMl = (ml > MAX_FILL_ML) ? MAX_FILL_ML : ml;
  }
  float maxMl() const { return _maxMl; }
  void setTimeoutMs(uint32_t ms) {
    if (ms >= 1000) _timeoutMs = (ms > POUR_TIMEOUT_MS) ? POUR_TIMEOUT_MS : ms;
  }

  bool relayOn() const { return _relayOn; }
  bool pouring() const { return _pouring; }
  bool finished() const { return _finished; }
  uint8_t finishResult() const { return _result; }
  uint8_t duty() const { return _duty; }
  uint8_t mode() const { return _mode; }
  float pouredMl() const { return _pouredMl; }
  float targetMl() const { return _targetMl; }

  void startPour(float targetMl, uint8_t mode, uint32_t nowMs) {
    _targetMl = targetMl;
    _mode = (mode > 1) ? 0 : mode;
    _pouredMl = 0.0f;
    _tStart = nowMs;
    _tLastAccum = nowMs;
    _zeroDutySince = 0;
    _pouring = true;
    _finished = false;
    _result = RUNNING;
    _duty = 100;
    _setRelay(true);
  }

  void finish(uint8_t result) {
    _setRelay(false);
    _duty = 0;
    if (_pouring) {
      _pouring = false;
      _finished = true;
      _result = result;
    }
  }

  void forceOff() {
    _setRelay(false);
    _duty = 0;
    if (_pouring) {
      _pouring = false;
      _finished = true;
      _result = STOP_OVER_VOLUME;             // .ino gửi đúng mã dừng riêng
    }
  }

  void setDuty(uint8_t pct, uint32_t nowMs) {
    if (pct > 100) pct = 100;
    if (pct > 0 && pct < PUMP_MIN_DUTY_PCT) pct = PUMP_MIN_DUTY_PCT;
    _duty = pct;
    if (pct == 0) {
      _setRelay(false);
      if (_pouring && _zeroDutySince == 0) _zeroDutySince = nowMs;
    } else {
      _zeroDutySince = 0;
      if (_pouring) _setRelay(true);
    }
  }

  void update(uint32_t nowMs) {
    if (!_pouring) {
      _setRelay(false);
      return;
    }

    // Băm chậm relay cơ ở chế độ Pi điều khiển khi duty <100%.
    if (_mode == 1 && _duty < 100) {
      bool on = false;
      if (_duty > 0) {
        uint32_t onTime = (PUMP_DUTY_CYCLE_MS * (uint32_t)_duty) / 100u;
        on = (nowMs % PUMP_DUTY_CYCLE_MS) < onTime;
      }
      _setRelay(on);
    } else if (_duty > 0) {
      _setRelay(true);
    }

    uint32_t dt = nowMs - _tLastAccum;
    if (_relayOn && dt > 0) {
      _pouredMl += _flowMlS * (float)dt / 1000.0f;
    }
    _tLastAccum = nowMs;

    if (_pouredMl > _maxMl * 1.05f) {
      finish(STOP_OVER_VOLUME);
      return;
    }
    if ((nowMs - _tStart) > _timeoutMs) {
      finish(STOP_TIMEOUT);
      return;
    }

    if (_mode == 0) {
      if (_targetMl > 0.0f && _pouredMl >= (_targetMl - IN_FLIGHT_ML)) {
        _pouredMl += IN_FLIGHT_ML;
        finish(DONE_TARGET);
      }
    } else if (_zeroDutySince != 0 &&
               (nowMs - _zeroDutySince) >= FINISH_ZERO_DUTY_MS) {
      finish(DONE_TARGET);                    // Pi giữ bơm tắt = đã đạt mức
    }
  }

 private:
  void _setRelay(bool on) {
    _relayOn = on;
#if RELAY_ACTIVE_HIGH
    digitalWrite(PIN_RELAY, on ? HIGH : LOW);
#else
    digitalWrite(PIN_RELAY, on ? LOW : HIGH);
#endif
  }

  bool _relayOn = false;
  bool _pouring = false;
  bool _finished = true;
  uint8_t _duty = 0;
  uint8_t _mode = 1;
  uint8_t _result = IDLE;
  float _flowMlS = FLOW_ML_PER_S;
  float _targetMl = 0.0f;
  float _pouredMl = 0.0f;
  float _maxMl = MAX_FILL_ML;
  uint32_t _timeoutMs = POUR_TIMEOUT_MS;
  uint32_t _tStart = 0;
  uint32_t _tLastAccum = 0;
  uint32_t _zeroDutySince = 0;
};

#endif  // CUPFILLER_PI_PUMP_H
