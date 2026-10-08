#include "device_fsm.h"

#include <string.h>

#include "board_io.h"
#include "link_io.h"
#include "uart_protocol.h"
#include "voice_input.h"

#ifndef FW_VERSION
#define FW_VERSION "1.0"
#endif

namespace cupfiller {

// ---------------------------------------------------------------------------
const char *dev_state_name(DevState s) {
  switch (s) {
    case DevState::BOOT: return "BOOT";
    case DevState::IDLE: return "IDLE";
    case DevState::WAIT_ACK: return "WAIT_ACK";
    case DevState::ARMED: return "ARMED";
    case DevState::FILLING: return "FILLING";
  }
  return "?";
}

static char *itoa_simple(int v, char *buf, size_t cap) {
  char tmp[16];
  int i = 0;
  bool neg = v < 0;
  if (neg) v = -v;
  do {
    tmp[i++] = (char)('0' + (v % 10));
    v /= 10;
  } while (v && i < 15);
  size_t k = 0;
  if (neg && k + 1 < cap) buf[k++] = '-';
  while (i > 0 && k + 1 < cap) buf[k++] = tmp[--i];
  buf[k] = '\0';
  return buf;
}

// ---------------------------------------------------------------------------
DeviceFSM::DeviceFSM()
    : state_(DevState::BOOT),
      t_state_(0),
      last_pc_ms_(0),
      pc_seen_(false),
      last_hb_ms_(0),
      last_cup_report_ms_(0),
      ack_fail_count_(0),
      wait_cup_cycle_(false),
      cup_raw_(!CUP_ACTIVE_LEVEL),
      cup_change_ms_(0),
      cup_present_(false),
      duty_(0),
      relay_on_(false),
      n_levels_(N_LEVEL_BUTTONS),
      level_ml_(kDefaultPresets[2]),
      beep_until_ms_(0),
      voice_pending_(VOICE_NONE),
      fake_now_(-1) {
  last_error_[0] = '\0';
  err_until_ms_ = 0;
  for (int i = 0; i < n_levels_; ++i) presets_[i] = kDefaultPresets[i];
  for (int i = 0; i < N_LEVEL_BUTTONS + 1; ++i) {
    btn_[i].raw = !BTN_ACTIVE_LEVEL;
    btn_[i].change_ms = 0;
    btn_[i].stable_active = false;
    btn_[i].press_ms = 0;
    btn_[i].long_fired = false;
  }
}

uint32_t DeviceFSM::now() const {
  return fake_now_ >= 0 ? (uint32_t)fake_now_ : io_millis();
}

void DeviceFSM::force_now(uint32_t ms) { fake_now_ = (int32_t)ms; }

bool DeviceFSM::pc_online() const {
  return pc_seen_ && ((uint32_t)(now() - last_pc_ms_) <= (uint32_t)PC_TIMEOUT_MS);
}

// ---------------------------------------------------------------------------
void DeviceFSM::begin() {
  // --- dau ra: RELAY tat truoc tien (tich cuc CAO -> ghi 0) ---
  io_pin_output((uint8_t)PIN_RELAY_PUMP);
  set_relay(false);
#if PIN_LED >= 0
  io_pin_output((uint8_t)PIN_LED);
#endif
#if PIN_BUZZER >= 0
  io_pin_output((uint8_t)PIN_BUZZER);
  io_write((uint8_t)PIN_BUZZER, BUZZER_ACTIVE_HIGH ? 0 : 1);
#endif

  // --- dau vao: cam bien coc + nut bam, tat ca INPUT_PULLUP, tich cuc THAP ---
  io_pin_input_pullup((uint8_t)PIN_CUP_SENSOR);
  for (int i = 0; i < n_levels_; ++i) io_pin_input_pullup(kLevelPins[i]);
  io_pin_input_pullup((uint8_t)PIN_BTN_STOP);

  voice_begin();

  const uint32_t t = now();
  t_state_ = t;
  cup_change_ms_ = t;
  for (int i = 0; i < N_LEVEL_BUTTONS + 1; ++i) btn_[i].change_ms = t;
  last_pc_ms_ = t;
  last_hb_ms_ = t;
  last_cup_report_ms_ = t;

  send_frame("BOOT", FW_VERSION);
  beep(t, 80);
  state_ = DevState::IDLE;
}

// ---------------------------------------------------------------------------
void DeviceFSM::loop() {
  const uint32_t t = now();

  poll_cup(t);
  poll_buttons(t);
  poll_voice(t);
  poll_link(t);

  // --- an toan lien lac: mat may tinh -> ngat bom, huy "cho phep rot" ---
  if (pc_seen_ && (uint32_t)(t - last_pc_ms_) > (uint32_t)PC_TIMEOUT_MS) {
    if (state_ == DevState::FILLING) abort_fill("PC_TIMEOUT", DevState::IDLE, t);
    else if (state_ == DevState::ARMED || state_ == DevState::WAIT_ACK) go(DevState::IDLE, t);
    set_error("PC_TIMEOUT");
  }

  // --- may trang thai ---
  switch (state_) {
    case DevState::BOOT:
      go(DevState::IDLE, t);
      break;

    case DevState::IDLE:
      set_duty(0, t);
      if (cup_present_ && pc_online() && !wait_cup_cycle_ &&
          (uint32_t)(t - last_cup_report_ms_) >= (uint32_t)CUP_RETRY_MS) {
        last_cup_report_ms_ = t;
        send_frame("CUP", "1");
        go(DevState::WAIT_ACK, t);
      }
      break;

    case DevState::WAIT_ACK:
      set_duty(0, t);
      if (!cup_present_) {
        on_cup(false, t);
      } else if ((uint32_t)(t - t_state_) > (uint32_t)ACK_TIMEOUT_MS) {
        set_error("ACK_TIMEOUT");
        ack_failed(t);
        go(DevState::IDLE, t);
      }
      break;

    case DevState::ARMED:
      set_duty(0, t);
      if (!cup_present_) on_cup(false, t);
      break;

    case DevState::FILLING:
      if (!cup_present_) {
        on_cup(false, t);  // cat relay ngay lap tuc
      } else if ((uint32_t)(t - t_state_) > (uint32_t)FILL_MAX_MS) {
        abort_fill("FILL_TIMEOUT", DevState::ARMED, t);
      }
      break;
  }

  apply_softpwm(t);
  send_hb(t);
  update_indicators(t);
}

// ---------------------------------------------------------------------------
void DeviceFSM::go(DevState s, uint32_t t) {
  state_ = s;
  t_state_ = t;
}

void DeviceFSM::set_error(const char *code) {
  strncpy(last_error_, code ? code : "?", sizeof(last_error_) - 1);
  last_error_[sizeof(last_error_) - 1] = '\0';
  err_until_ms_ = now() + 1500;
  char args[40];
  strncpy(args, last_error_, sizeof(args) - 1);
  args[sizeof(args) - 1] = '\0';
  send_frame("ERR", args);
}

// ---------------------------------------------------------------------------
// CAM BIEN COC (loc rung CUP_DEBOUNCE_MS)
void DeviceFSM::poll_cup(uint32_t t) {
  const int lvl = io_read((uint8_t)PIN_CUP_SENSOR);
  if (lvl != cup_raw_) {
    cup_raw_ = lvl;
    cup_change_ms_ = t;
    return;
  }
  const bool present = (lvl == CUP_ACTIVE_LEVEL);
  if (present != cup_present_ && (uint32_t)(t - cup_change_ms_) >= (uint32_t)CUP_DEBOUNCE_MS) {
    cup_present_ = present;
    on_cup(present, t);
  }
}

void DeviceFSM::on_cup(bool present, uint32_t t) {
  if (!present) {
    cup_present_ = false;
    ack_fail_count_ = 0;    // nhac coc ra -> xoa loi cu, cho phep thu lai
    wait_cup_cycle_ = false;
    if (state_ == DevState::FILLING) {
      abort_fill("CUP_REMOVED", DevState::IDLE, t);  // cat bom truoc, bao sau
    } else if (state_ != DevState::IDLE) {
      go(DevState::IDLE, t);
    }
    send_frame("CUP", "0");
    return;
  }
  // co coc: bao ngay cho may tinh de no BAT CAMERA (neu dang ket noi)
  if (state_ == DevState::IDLE) {
    if (pc_online()) {
      last_cup_report_ms_ = t;
      send_frame("CUP", "1");
      go(DevState::WAIT_ACK, t);
    } else {
      last_cup_report_ms_ = t - CUP_RETRY_MS + 200;  // doi PC online thi bao ngay
    }
  }
}

// ---------------------------------------------------------------------------
// NUT BAM: tich cuc THAP, INPUT_PULLUP, loc rung, phan biet nhan nhan / giu lau
void DeviceFSM::poll_buttons(uint32_t t) {
  const int n = n_levels_ + 1;
  for (int i = 0; i < n; ++i) {
    const uint8_t pin = (i < n_levels_) ? kLevelPins[i] : (uint8_t)PIN_BTN_STOP;
    const int lvl = io_read(pin);
    Btn &b = btn_[i];
    if (lvl != b.raw) {
      b.raw = lvl;
      b.change_ms = t;
      continue;
    }
    const bool active = (lvl == BTN_ACTIVE_LEVEL);
    if (active != b.stable_active && (uint32_t)(t - b.change_ms) >= (uint32_t)BTN_DEBOUNCE_MS) {
      b.stable_active = active;
      if (active) {
        b.press_ms = t;
        b.long_fired = false;
      } else {
        // tha nut ra
        if (i == n_levels_) {
          on_stop_button(t);
        } else if (!b.long_fired) {
          on_level_button(i, false, t);  // nhan nhanh = chon muc + bat dau rot
        }
      }
    }
    // giu lau nut muc: chi chon muc, khong rot
    if (i < n_levels_ && b.stable_active && !b.long_fired &&
        (uint32_t)(t - b.press_ms) >= (uint32_t)BTN_LONG_PRESS_MS) {
      b.long_fired = true;
      on_level_button(i, true, t);
    }
  }
}

// May tinh khong xac nhan duoc coc: lui lai CUP_RETRY_MS roi thu lai;
// that bai 3 lan lien tiep -> doi nguoi dung NHAC COC RA roi dat lai
// (tranh bao lien tuc lam loang UART va nhap nhay LED mai mai).
void DeviceFSM::ack_failed(uint32_t t) {
  last_cup_report_ms_ = t;
  if (++ack_fail_count_ >= 3) wait_cup_cycle_ = true;
}

void DeviceFSM::on_level_button(int idx, bool long_press, uint32_t t) {
  const int ml = preset(idx);
  char args[16];
  char id[4];
  itoa_simple(idx + 1, id, sizeof(id));
  strcpy(args, id);
  strcat(args, long_press ? ",LONG" : ",DOWN");
  send_frame("BTN", args);
  if (long_press) {
    level_ml_ = ml;
    char buf[8];
    send_frame("LVL", itoa_simple(ml, buf, sizeof(buf)));
    return;
  }
  on_voice(VOICE_LEVEL_1 + idx, t);  // dung chung duong xu ly voi mic
}

void DeviceFSM::on_stop_button(uint32_t t) {
  send_frame("BTN", "0,DOWN");
  if (state_ == DevState::FILLING) {
    abort_fill("BTN_STOP", DevState::ARMED, t);
    beep(t, 120);
  } else {
    send_frame("STOP", "BTN_IDLE");
  }
}

// ---------------------------------------------------------------------------
// GIONG NOI (mic / module nhan lenh giong noi)
void DeviceFSM::poll_voice(uint32_t t) {
  int cmd = voice_pending_;
  voice_pending_ = VOICE_NONE;
  const int v = voice_poll(t);
  if (v != VOICE_NONE) cmd = v;
  if (cmd != VOICE_NONE) on_voice(cmd, t);
}

void DeviceFSM::inject_voice(int cmd) { voice_pending_ = cmd; }

void DeviceFSM::on_voice(int cmd, uint32_t t) {
  char buf[8];
  if (cmd >= VOICE_LEVEL_1 && cmd <= VOICE_LEVEL_5) {
    const int idx = cmd - VOICE_LEVEL_1;
    if (idx >= n_levels_) return;
    const int ml = preset(idx);
    level_ml_ = ml;
    send_frame("LVL", itoa_simple(ml, buf, sizeof(buf)));
    start_fill(ml, t);
    return;
  }
  if (cmd == VOICE_START) {
    start_fill(level_ml_, t);
    return;
  }
  if (cmd == VOICE_STOP) {
    if (state_ == DevState::FILLING) {
      abort_fill("VOICE_STOP", DevState::ARMED, t);
      beep(t, 120);
    } else {
      send_frame("STOP", "VOICE_IDLE");
    }
  }
}

void DeviceFSM::start_fill(int ml, uint32_t t) {
  if (state_ == DevState::FILLING) {
    send_frame("ERR", "BUSY");
    return;
  }
  if (state_ != DevState::ARMED) {
    // Chua duoc may tinh xac nhan coc -> KHONG duoc bom
    send_frame("ERR", state_ == DevState::WAIT_ACK ? "NOT_ACKED" : "NOT_ARMED");
    return;
  }
  if (!pc_online()) {
    set_error("PC_TIMEOUT");
    go(DevState::IDLE, t);
    return;
  }
  level_ml_ = ml;
  char buf[8];
  send_frame("START", itoa_simple(ml, buf, sizeof(buf)));
  beep(t, 60);
  go(DevState::FILLING, t);
}

void DeviceFSM::abort_fill(const char *why, DevState next, uint32_t t) {
  set_duty(0, t);      // cat relay TRUOC
  set_relay(false);
  send_frame("STOP", why);
  go(next, t);
}

// ---------------------------------------------------------------------------
// XU LY TIN HIEU TU MAY TINH
void DeviceFSM::poll_link(uint32_t t) {
  uint8_t buf[64];
  for (int guard = 0; guard < 8; ++guard) {
    const size_t n = link_read(buf, sizeof(buf));
    if (n == 0) break;
    for (size_t i = 0; i < n; ++i) {
      if (rx_.feed(buf[i])) handle_frame(rx_.type(), rx_.args(), t);
    }
  }
}

void DeviceFSM::handle_frame(const char *type, const char *args, uint32_t t) {
  // moi tin hop le deu "nuoi" watchdog
  last_pc_ms_ = t;
  pc_seen_ = true;

  if (strcmp(type, "HELLO") == 0) {
    send_frame("BOOT", FW_VERSION);
    send_hb(t);
    if (cup_present_ && state_ == DevState::IDLE) {
      last_cup_report_ms_ = t;
      send_frame("CUP", "1");
      go(DevState::WAIT_ACK, t);
    }
    return;
  }

  if (strcmp(type, "CFG") == 0) {
    // CFG,100;150;200;250;300  (dung ';' vi ',' la dau phan cach tham so)
    int i = 0;
    const char *p = args;
    while (*p && i < N_LEVEL_BUTTONS) {
      int v = 0;
      bool any = false;
      while (*p >= '0' && *p <= '9') { v = v * 10 + (*p - '0'); ++p; any = true; }
      if (any && v > 0 && v <= 2000) presets_[i++] = v;
      while (*p && *p != ';') ++p;
      if (*p == ';') ++p;
    }
    if (i > 0) n_levels_ = i;
    level_ml_ = presets_[0];
    send_frame("CFG", "OK");
    return;
  }

  if (strcmp(type, "ACK") == 0) {
    const int ok = get_arg_int(args, 0, 0);
    if (ok) {
      if (state_ == DevState::WAIT_ACK) {
        go(DevState::ARMED, t);
        last_error_[0] = '\0';
        ack_fail_count_ = 0;
        wait_cup_cycle_ = false;
        beep(t, 80);
      }
    } else {
      if (state_ == DevState::FILLING) abort_fill("ACK_LOST", DevState::IDLE, t);
      set_error("NO_CUP_VISION");
      ack_failed(t);
      go(DevState::IDLE, t);
    }
    return;
  }

  if (strcmp(type, "PUMP") == 0) {
    if (state_ == DevState::FILLING) {
      int d = get_arg_int(args, 0, 0);
      if (d < 0) d = 0;
      if (d > 100) d = 100;
      set_duty((uint8_t)d, t);
    } else {
      set_duty(0, t);  // khong dang rot -> khong bao gio bat bom
    }
    return;
  }

  if (strcmp(type, "STATE") == 0) {
    char s[16];
    get_arg(args, 0, s, sizeof(s));
    if (strcmp(s, "DONE") == 0) {
      set_duty(0, t);
      set_relay(false);
      if (state_ == DevState::FILLING) go(DevState::ARMED, t);
      char ms[12];
      send_frame("DONE", itoa_simple((int)(t - t_state_), ms, sizeof(ms)));
      beep(t, 180);
      return;
    }
    if (strcmp(s, "FAULT") == 0) {
      set_duty(0, t);
      set_relay(false);
      if (state_ == DevState::FILLING) go(DevState::ARMED, t);
      set_error("PC_FAULT");
      return;
    }
    return;  // cac trang thai khac chi de hien thi
  }

  // HB va cac tin khac: chi can nuoi watchdog (da lam o dau ham)
}

// ---------------------------------------------------------------------------
void DeviceFSM::set_duty(uint8_t d, uint32_t t) {
  (void)t;
  if (d == duty_) return;
  duty_ = d;
  char buf[6];
  send_frame("PUMP", itoa_simple(d, buf, sizeof(buf)));
}

void DeviceFSM::set_relay(bool on) {
  if (on == relay_on_) return;
  relay_on_ = on;
#if RELAY_ACTIVE_HIGH
  io_write((uint8_t)PIN_RELAY_PUMP, on ? 1 : 0);
#else
  io_write((uint8_t)PIN_RELAY_PUMP, on ? 0 : 1);
#endif
}

// Relay khong the bam nhanh nhu MOSFET -> bam mem voi chu ky dai.
void DeviceFSM::apply_softpwm(uint32_t t) {
  bool on;
  if (state_ != DevState::FILLING || duty_ == 0) {
    on = false;
  } else if (duty_ >= 100) {
    on = true;
  } else {
    const uint32_t period = (uint32_t)SOFTPWM_PERIOD_MS;
    const uint32_t phase = t % period;
    on = phase < (period * (uint32_t)duty_) / 100u;
  }
  set_relay(on);
}

void DeviceFSM::send_hb(uint32_t t) {
  if ((uint32_t)(t - last_hb_ms_) < (uint32_t)HB_PERIOD_MS) return;
  last_hb_ms_ = t;
  char args[48];
  char up[12], cup[4], arm[4], du[6];
  itoa_simple((int)t, up, sizeof(up));
  strcpy(args, up);
  strcat(args, ",");
  strcat(args, state_name());
  strcat(args, ",");
  strcat(args, itoa_simple(cup_present_ ? 1 : 0, cup, sizeof(cup)));
  strcat(args, ",");
  strcat(args, itoa_simple(armed() ? 1 : 0, arm, sizeof(arm)));
  strcat(args, ",");
  strcat(args, itoa_simple(duty_, du, sizeof(du)));
  send_frame("HB", args);
}

void DeviceFSM::update_indicators(uint32_t t) {
  bool led = false;
  switch (state_) {
    case DevState::BOOT: led = false; break;
    case DevState::IDLE: led = ((t / 600u) & 1u) == 0u; break;         // chap cham
    case DevState::WAIT_ACK: led = ((t / 120u) & 1u) == 0u; break;     // chap nhanh
    case DevState::ARMED: led = true; break;                           // sang dung
    case DevState::FILLING: led = relay_on_ || (((t / 200u) & 1u) == 0u); break;
  }
  if (t < err_until_ms_) led = ((t / 90u) & 1u) == 0u;                 // bao loi: nhap nhay nhanh
#if PIN_LED >= 0
#if LED_ACTIVE_HIGH
  io_write((uint8_t)PIN_LED, led ? 1 : 0);
#else
  io_write((uint8_t)PIN_LED, led ? 0 : 1);
#endif
#endif

#if PIN_BUZZER >= 0
  const bool bz = (t < beep_until_ms_);
  io_write((uint8_t)PIN_BUZZER, bz ? (BUZZER_ACTIVE_HIGH ? 1 : 0) : (BUZZER_ACTIVE_HIGH ? 0 : 1));
#endif
}

void DeviceFSM::beep(uint32_t t, uint32_t ms) {
  if (t + ms > beep_until_ms_) beep_until_ms_ = t + ms;
}

}  // namespace cupfiller
