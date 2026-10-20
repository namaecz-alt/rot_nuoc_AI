// ===========================================================================
//  inputs.h - CẢM BIẾN + START + 3 NÚT MỨC (đã chống dội)
//
//  Thứ tự bit/kênh dùng khắp firmware và trong INPUT_STATE:
//      bit0/kênh0: cảm biến GPIO34
//      bit1/kênh1: START GPIO26
//      bit2/kênh2: mức 1 GPIO32 (30%)
//      bit3/kênh3: mức 2 GPIO33 (60%)
//      bit4/kênh4: mức 3 GPIO25 (90%)
//
//  Giá trị logic sau lớp này luôn là: 1 = cảm biến tác động / nút đang nhấn.
//  Riêng điện: nút nhấn kéo chân xuống LOW (INPUT_PULLUP). GPIO34 không có
//  pull-up nội nên chỉ đặt INPUT; cực tính cảm biến do SENSOR_ACTIVE_LOW chọn.
// ===========================================================================
#ifndef CUPFILLER_PI_INPUTS_H
#define CUPFILLER_PI_INPUTS_H

#include <Arduino.h>
#include "pi_config.h"

static constexpr uint8_t IN_SENSOR = 0;
static constexpr uint8_t IN_START  = 1;
static constexpr uint8_t IN_LEVEL0 = 2;

struct InputSnapshot {
  bool sensor = false;
  bool start = false;
  bool level[N_LEVEL_BUTTONS] = {false, false, false};
};

struct InputEvents {
  bool changed = false;
  uint8_t pressedMask = 0;
  uint8_t releasedMask = 0;
  uint16_t heldMs[N_INPUTS] = {0, 0, 0, 0, 0};
  int8_t estopChannel = -1;                    // -1 = không có dừng khẩn

  void clear() {
    changed = false;
    pressedMask = 0;
    releasedMask = 0;
    estopChannel = -1;
    for (uint8_t i = 0; i < N_INPUTS; i++) heldMs[i] = 0;
  }
};

class InputBank {
 public:
  void begin(uint32_t nowMs) {
    _ch[IN_SENSOR] = Channel{PIN_CUP_SENSOR, SENSOR_ACTIVE_LOW != 0,
                             SENSOR_DEBOUNCE_MS, false};
    _ch[IN_START]  = Channel{PIN_START_BUTTON, true, BUTTON_DEBOUNCE_MS, true};
    for (uint8_t i = 0; i < N_LEVEL_BUTTONS; i++) {
      _ch[IN_LEVEL0 + i] = Channel{LEVEL_BUTTON_PINS[i], true,
                                   BUTTON_DEBOUNCE_MS, true};
    }

    // GPIO34 input-only/không pull-up nội. Các nút còn lại dùng INPUT_PULLUP.
    pinMode(PIN_CUP_SENSOR, INPUT);
    pinMode(PIN_START_BUTTON, INPUT_PULLUP);
    for (uint8_t i = 0; i < N_LEVEL_BUTTONS; i++) {
      pinMode(LEVEL_BUTTON_PINS[i], INPUT_PULLUP);
    }

    for (uint8_t i = 0; i < N_INPUTS; i++) {
      bool raw = _readRaw(_ch[i]);
      _ch[i].lastRaw = raw;
      _ch[i].stable = raw;
      _ch[i].rawChangedMs = nowMs;
      _ch[i].pressedMs = nowMs;
      _ch[i].estopFired = false;
    }
  }

  InputSnapshot snapshot() const {
    InputSnapshot s;
    s.sensor = _ch[IN_SENSOR].stable;
    s.start = _ch[IN_START].stable;
    for (uint8_t i = 0; i < N_LEVEL_BUTTONS; i++) {
      s.level[i] = _ch[IN_LEVEL0 + i].stable;
    }
    return s;
  }

  bool channel(uint8_t i) const {
    return i < N_INPUTS && _ch[i].stable;
  }

  void update(uint32_t nowMs, InputEvents &ev) {
    ev.clear();
    for (uint8_t i = 0; i < N_INPUTS; i++) {
      Channel &c = _ch[i];
      bool raw = _readRaw(c);

      if (raw != c.lastRaw) {
        c.lastRaw = raw;
        c.rawChangedMs = nowMs;
      }
      if ((nowMs - c.rawChangedMs) < c.debounceMs) {
        // Chưa ổn định; vẫn xét E-stop ở dưới bằng trạng thái stable cũ.
      } else if (raw != c.stable) {
        c.stable = raw;
        ev.changed = true;
        if (raw) {
          c.pressedMs = nowMs;
          c.estopFired = false;
          ev.pressedMask |= (uint8_t)(1u << i);
        } else {
          uint32_t held = nowMs - c.pressedMs;
          ev.heldMs[i] = (uint16_t)min(held, (uint32_t)65000);
          ev.releasedMask |= (uint8_t)(1u << i);
        }
      }

      if (c.canEstop && c.stable && !c.estopFired &&
          (nowMs - c.pressedMs) >= BUTTON_ESTOP_MS) {
        c.estopFired = true;
        if (ev.estopChannel < 0) ev.estopChannel = (int8_t)i;
        ev.heldMs[i] = (uint16_t)min(nowMs - c.pressedMs, (uint32_t)65000);
      }
    }
  }

 private:
  struct Channel {
    Channel() {}
    Channel(uint8_t p, bool al, uint16_t debounce, bool estop)
        : pin(p), activeLow(al), debounceMs(debounce), canEstop(estop) {}

    uint8_t pin = 0;
    bool activeLow = true;
    uint16_t debounceMs = 25;
    bool canEstop = false;

    bool lastRaw = false;
    bool stable = false;
    bool estopFired = false;
    uint32_t rawChangedMs = 0;
    uint32_t pressedMs = 0;
  };

  static bool _readRaw(const Channel &c) {
    int v = digitalRead(c.pin);
    return c.activeLow ? (v == LOW) : (v == HIGH);
  }

  Channel _ch[N_INPUTS];
};

#endif  // CUPFILLER_PI_INPUTS_H
