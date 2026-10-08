// ===========================================================================
//  pump.h - ĐIỀU KHIỂN BƠM QUA RELAY  (RELAY TÍCH CỰC MỨC CAO)
//
//  Hai chế độ:
//    * ESP tự đong (MODE_ESP_OPEN_LOOP): relay BẬT 100%, ESP tự cộng dồn thể tích
//      theo lưu lượng đã hiệu chuẩn (FLOW_ML_PER_S) rồi ngắt khi đủ ml.
//    * PC điều khiển (MODE_PC_CLOSED_LOOP): PC gửi PUMP_SET duty 0..100%; relay cơ
//      không băm được nhanh nên firmware BĂM CHẬM theo DUTY_CYCLE_MS (ví dụ duty
//      30% = bật 600 ms / tắt 1400 ms), nhờ đó vẫn điều chỉnh được lưu lượng.
//
//  An toàn do ESP tự lo, KHÔNG phụ thuộc PC:
//    - quá MAX_FILL_ML         -> ngắt (OVER_VOLUME)
//    - quá POUR_TIMEOUT_MS     -> ngắt (TIMEOUT)
//    - mất cốc / mất liên lạc  -> ngắt (do .ino gọi forceOff)
// ===========================================================================
#ifndef CUPFILLER_PUMP_H
#define CUPFILLER_PUMP_H

#include <Arduino.h>
#include "config.h"

class PumpDriver {
 public:
  enum Result { RUNNING = 0, DONE_TARGET = 1, STOP_OVER_VOLUME = 2, STOP_TIMEOUT = 3 };

  void begin() {
    pinMode(PIN_RELAY, OUTPUT);
    forceOff();
#if RELAY_USE_PWM
    // Băm xung nhanh cho MOSFET (relay cơ KHÔNG dùng được chế độ này)
    _pwmFreq = RELAY_PWM_HZ;
#endif
    _flow = FLOW_ML_PER_S;
    _maxMl = MAX_FILL_ML;
    _timeoutMs = POUR_TIMEOUT_MS;
  }

  // ---- cấu hình -----------------------------------------------------
  void setFlow(float mlPerS) { if (mlPerS > 0.1f) _flow = mlPerS; }
  float flow() const { return _flow; }
  void setMaxMl(float ml) { if (ml > 0.0f) _maxMl = ml; }
  float maxMl() const { return _maxMl; }
  void setInFlight(float ml) { _inFlight = ml; }
  void setTimeoutMs(uint32_t ms) { if (ms > 1000) _timeoutMs = ms; }

  // ---- trạng thái ---------------------------------------------------
  bool isOn() const { return _relayOn; }
  uint8_t duty() const { return _duty; }
  float pouredMl() const { return _poured; }
  float targetMl() const { return _target; }
  uint8_t mode() const { return _mode; }
  bool finished() const { return _finished; }
  uint8_t finishResult() const { return _result; }
  uint32_t pourElapsedMs(uint32_t nowMs) const { return _pouring ? (nowMs - _tStart) : 0; }

  // ---- bắt đầu / kết thúc lượt rót ----------------------------------
  void startPour(float targetMl, uint8_t mode, uint32_t nowMs) {
    _target = targetMl;
    _mode = mode;
    _poured = 0.0f;
    _tStart = nowMs;
    _tLastAccum = nowMs;
    _lastOnMs = nowMs;
    _pouring = true;
    _finished = false;
    _result = RUNNING;
    _zeroDutySince = 0;
    _duty = 100;
    _setRelay(true);
  }

  void finish(uint8_t result) {
    if (!_pouring) return;
    _setRelay(false);
    _pouring = false;
    _finished = true;
    _result = result;
    _duty = 0;
  }

  void forceOff() {                       // dùng cho lỗi / mất cốc / mất liên lạc
    _setRelay(false);
    _duty = 0;
    if (_pouring) {
      _pouring = false;
      _finished = true;
      _result = STOP_OVER_VOLUME;         // .ino sẽ gửi mã dừng riêng cho PC
    }
  }

  // ---- PC điều khiển trực tiếp (PUMP_SET) ---------------------------
  void setDuty(uint8_t pct, uint32_t nowMs) {
    if (pct > 100) pct = 100;
    if (pct > 0 && pct < FLOW_MIN_DUTY_PCT) pct = FLOW_MIN_DUTY_PCT;
    _duty = pct;
    if (pct == 0) {
      _setRelay(false);
      if (_pouring && _zeroDutySince == 0) _zeroDutySince = nowMs;
    } else {
      _zeroDutySince = 0;
      if (_pouring) {
        _setRelay(true);
        _lastOnMs = nowMs;
      }
    }
  }

  // ------------------------------------------------------------------
  //  Gọi đều đặn trong loop(): băm relay theo duty + cộng dồn thể tích + an toàn
  // ------------------------------------------------------------------
  uint8_t update(uint32_t nowMs) {
    if (!_pouring) {
      _setRelay(false);
      return DONE_TARGET;
    }

    // (1) băm relay theo duty (chỉ khi PC điều khiển và duty < 100)
    if (_mode == 1 /* PC_CLOSED_LOOP */ && _duty < 100) {
      bool on;
      if (_duty == 0) {
        on = false;
      } else {
        const uint32_t cycle = DUTY_CYCLE_MS;
        const uint32_t onTime = (cycle * (uint32_t)_duty) / 100u;
        const uint32_t phase = nowMs % cycle;      // giống esp_sim.py bên PC
        on = phase < onTime;
      }
      _setRelay(on);
    } else if (_duty > 0) {
      _setRelay(true);
    }

    // (2) cộng dồn thể tích theo THỜI GIAN RELAY BẬT (bám đúng lưu lượng thật)
    uint32_t dt = nowMs - _tLastAccum;
    if (_relayOn && dt > 0) {
      _poured += _flow * (float)dt / 1000.0f;
    }
    _tLastAccum = nowMs;

    // (3) AN TOÀN tuyệt đối
    if (_poured > _maxMl * 1.05f) {
      finish(STOP_OVER_VOLUME);
      return STOP_OVER_VOLUME;
    }
    if ((nowMs - _tStart) > _timeoutMs) {
      finish(STOP_TIMEOUT);
      return STOP_TIMEOUT;
    }

    // (4) điều kiện kết thúc
    if (_mode == 0 /* ESP đong */) {
      if (_poured >= (_target - _inFlight)) {
        _poured += _inFlight;             // nước còn đang rơi trong ống
        finish(DONE_TARGET);
        return DONE_TARGET;
      }
    } else {
      if (_zeroDutySince != 0 && (nowMs - _zeroDutySince) >= FINISH_ZERO_DUTY_MS) {
        finish(DONE_TARGET);              // PC giữ duty 0 -> kết thúc lượt rót
        return DONE_TARGET;
      }
    }
    return RUNNING;
  }

 private:
  void _setRelay(bool on) {
    _relayOn = on;
#if RELAY_ACTIVE_HIGH
    digitalWrite(PIN_RELAY, on ? HIGH : LOW);
#else
    digitalWrite(PIN_RELAY, on ? LOW : HIGH);
#endif
#if RELAY_USE_PWM
    if (on) {
      // giữ nguyên mức cao; nếu cần PWM thật thì bật ở đây (xem README)
    }
#endif
  }

  bool _relayOn = false;
  bool _pouring = false;
  bool _finished = true;
  uint8_t _duty = 0;
  uint8_t _mode = 0;
  uint8_t _result = RUNNING;
  float _flow = FLOW_ML_PER_S;
  float _target = 200.0f;
  float _poured = 0.0f;
  float _maxMl = MAX_FILL_ML;
  float _inFlight = IN_FLIGHT_ML;
  uint32_t _timeoutMs = POUR_TIMEOUT_MS;
  uint32_t _tStart = 0;
  uint32_t _tLastAccum = 0;
  uint32_t _lastOnMs = 0;
  uint32_t _zeroDutySince = 0;
  uint32_t _pwmFreq = RELAY_PWM_HZ;
};

#endif  // CUPFILLER_PUMP_H
