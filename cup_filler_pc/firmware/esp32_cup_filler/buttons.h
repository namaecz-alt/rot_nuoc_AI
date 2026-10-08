// ===========================================================================
//  buttons.h - 5 NÚT CHỌN MỨC NƯỚC  (TÍCH CỰC MỨC THẤP, chân INPUT_PULLUP)
//
//  Nhấn = chân về LOW (nối GND), thả = HIGH nhờ điện trở kéo lên trong chip.
//  Phân loại theo thời gian giữ:
//      ngắn  (< BUTTON_LONG_MS)   -> chọn mức + bật bơm (nếu BUTTON_STARTS_POUR)
//      dài   (>= BUTTON_LONG_MS)  -> chỉ chọn mức, CHƯA bơm (bấm lại để bơm)
//      rất dài (>= BUTTON_ESTOP_MS)-> DỪNG KHẨN CẤP: ngắt bơm NGAY khi đang giữ
// ===========================================================================
#ifndef CUPFILLER_BUTTONS_H
#define CUPFILLER_BUTTONS_H

#include <Arduino.h>
#include "config.h"

class ButtonBank {
 public:
  enum Event { EV_NONE = 0, EV_SHORT = 1, EV_LONG = 2, EV_ESTOP = 3 };

  void begin() {
    for (uint8_t i = 0; i < N_BUTTONS; i++) {
      pinMode(BUTTON_PINS[i], INPUT_PULLUP);      // kéo lên nội -> nhấn là LOW
      _stable[i] = true;                          // true = đang THẢ (mức HIGH)
      _lastRead[i] = true;
      _changeMs[i] = 0;
      _pressMs[i] = 0;
      _estopFired[i] = false;
    }
  }

  // Gọi mỗi vòng loop(). Trả về sự kiện vừa xảy ra kèm CHỈ SỐ nút và SỐ MS ĐÃ GIỮ.
  // Sự kiện DỪNG KHẨN CẤP được trả NGAY khi giữ đủ lâu (không cần chờ thả tay).
  Event update(uint32_t nowMs, uint8_t &indexOut, uint16_t &heldMsOut) {
    Event result = EV_NONE;
    indexOut = 0xFF;
    heldMsOut = 0;
    for (uint8_t i = 0; i < N_BUTTONS; i++) {
      bool raw = (digitalRead(BUTTON_PINS[i]) == HIGH);   // true = thả
      if (raw != _lastRead[i]) {                          // có dấu hiệu dội/nhấn
        _lastRead[i] = raw;
        _changeMs[i] = nowMs;
      }
      if ((nowMs - _changeMs[i]) < BUTTON_DEBOUNCE_MS) continue;   // chờ ổn định
      if (raw != _stable[i]) {
        _stable[i] = raw;
        if (!raw) {                                       // vừa NHẤN
          _pressMs[i] = nowMs;
          _estopFired[i] = false;
        } else {                                          // vừa THẢ
          uint32_t held = nowMs - _pressMs[i];
          indexOut = i;
          heldMsOut = (uint16_t)min(held, (uint32_t)65000);
          if (held >= BUTTON_ESTOP_MS) result = EV_ESTOP;
          else if (held >= BUTTON_LONG_MS) result = EV_LONG;
          else result = EV_SHORT;
          return result;                                  // mỗi lần chỉ trả 1 sự kiện
        }
      }
      // nhấn giữ đủ lâu -> DỪNG KHẨN CẤP ngay (không cần thả tay)
      if (!_stable[i] && !_estopFired[i] && (nowMs - _pressMs[i]) >= BUTTON_ESTOP_MS) {
        _estopFired[i] = true;
        indexOut = i;
        heldMsOut = (uint16_t)min(nowMs - _pressMs[i], (uint32_t)65000);
        return EV_ESTOP;
      }
    }
    return result;
  }

  // Dùng cho phần cứng khác / kiểm thử: đọc thô trạng thái nhấn
  static bool isPressed(uint8_t i) {
    return i < N_BUTTONS && digitalRead(BUTTON_PINS[i]) == LOW;
  }

 private:
  bool _stable[N_BUTTONS];
  bool _lastRead[N_BUTTONS];
  bool _estopFired[N_BUTTONS];
  uint32_t _changeMs[N_BUTTONS];
  uint32_t _pressMs[N_BUTTONS];
};

#endif  // CUPFILLER_BUTTONS_H
