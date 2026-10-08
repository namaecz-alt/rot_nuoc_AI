// =====================================================================
//  MAY TRANG THAI CUA ESP32 (khong phu thuoc Arduino -> unit-test tren PC)
//
//  Luong hoat dong (dung theo yeu cau de bai):
//
//    IDLE      : chi doc cam bien coc. Thay coc -> gui $CUP,1*xx len may tinh.
//    WAIT_ACK  : doi may tinh BAT CAMERA, nhan dien coc va tra loi $ACK,1.
//    ARMED     : da duoc may tinh xac nhan -> CHO PHEP nhan nut muc / noi mic.
//    FILLING   : da gui $START,ml. Bom chay theo lenh $PUMP,duty cua may tinh.
//
//  An toan:
//    * Mat lien lac voi may tinh (khong co $HB) qua PC_TIMEOUT_MS -> NGAT RELAY.
//    * Coc bi nha bat ky luc dang rot -> NGAT RELAY ngay lap tuc, gui $STOP.
//    * Relay tich cuc CAO va duoc tat ngay trong begin() -> bom khong tu chay.
// =====================================================================
#ifndef CUPFILLER_DEVICE_FSM_H
#define CUPFILLER_DEVICE_FSM_H

#include <stdint.h>

#include "config.h"
#include "uart_protocol.h"
#include "voice_input.h"

namespace cupfiller {

enum class DevState { BOOT, IDLE, WAIT_ACK, ARMED, FILLING };

// Ma lenh giong noi (VoiceCmd) nam trong voice_input.h - nut bam va mic
// dung CHUNG mot bo ma lenh.

const char *dev_state_name(DevState s);

class DeviceFSM {
 public:
  DeviceFSM();

  void begin();   // khoi tao chan, tat relay, gui $BOOT
  void loop();    // goi lien tuc

  // ---- truy van ----
  DevState state() const { return state_; }
  const char *state_name() const { return dev_state_name(state_); }
  bool cup_present() const { return cup_present_; }     // da loc rung
  bool armed() const { return state_ == DevState::ARMED || state_ == DevState::FILLING; }
  int level_ml() const { return level_ml_; }
  uint8_t duty() const { return duty_; }                // 0..100 may tinh dat
  bool relay_on() const { return relay_on_; }           // muc relay that (soft-PWM)
  bool pc_online() const;
  uint32_t link_errors() const { return rx_.errors(); }
  const char *last_error() const { return last_error_; }
  int preset(int i) const { return (i >= 0 && i < n_levels_) ? presets_[i] : 0; }

  // ---- dung cho test / giao dien khac ----
  void inject_voice(int cmd);          // gia lap mot lenh giong noi
  void force_now(uint32_t ms);         // (chi host) dat dong ho ao

 private:
  // dau vao
  void poll_cup(uint32_t now);
  void poll_buttons(uint32_t now);
  void poll_voice(uint32_t now);
  void poll_link(uint32_t now);

  // xu ly
  void handle_frame(const char *type, const char *args, uint32_t now);
  void on_cup(bool present, uint32_t now);
  void on_level_button(int idx, bool long_press, uint32_t now);
  void on_stop_button(uint32_t now);
  void on_voice(int cmd, uint32_t now);
  void ack_failed(uint32_t now);
  void start_fill(int ml, uint32_t now);
  void abort_fill(const char *why, DevState next, uint32_t now);
  void go(DevState s, uint32_t now);

  // dau ra
  void set_relay(bool on);
  void set_duty(uint8_t d, uint32_t now);
  void apply_softpwm(uint32_t now);
  void send_hb(uint32_t now);
  void update_indicators(uint32_t now);
  void set_error(const char *code);
  void beep(uint32_t now, uint32_t ms);

  // trang thai
  DevState state_;
  uint32_t t_state_;          // millis luc vao trang thai hien tai
  uint32_t last_pc_ms_;       // millis lan cuoi nhan duoc tin tu may tinh
  bool pc_seen_;
  uint32_t last_hb_ms_;
  uint32_t last_cup_report_ms_;
  int ack_fail_count_;        // so lan may tinh bao "khong thay coc" lien tiep
  bool wait_cup_cycle_;       // that bai nhieu lan -> doi nguoi dung nha coc ra
  char last_error_[24];
  uint32_t err_until_ms_;

  // cam bien coc (loc rung)
  int cup_raw_;               // muc doc duoc gan nhat
  uint32_t cup_change_ms_;
  bool cup_present_;

  // nut bam
  struct Btn {
    int raw;
    uint32_t change_ms;
    bool stable_active;
    uint32_t press_ms;
    bool long_fired;
  };
  Btn btn_[N_LEVEL_BUTTONS + 1];  // + nut STOP (chi so N_LEVEL_BUTTONS)

  // bom
  uint8_t duty_;
  bool relay_on_;

  // muc rot
  int presets_[N_LEVEL_BUTTONS];
  int n_levels_;
  int level_ml_;

  // chi bao
  uint32_t beep_until_ms_;

  // bien dem dung chung cho poll_voice (tranh cap phat dong)
  int voice_pending_;

  // bo giai ma khung tin den tu may tinh
  FrameParser rx_;

  // host-only: dong ho ao (-1 = dung io_millis())
  int32_t fake_now_;
  uint32_t now() const;
};

}  // namespace cupfiller

#endif  // CUPFILLER_DEVICE_FSM_H
