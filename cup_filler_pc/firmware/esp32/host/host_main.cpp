// =====================================================================
//  "ESP32 AO" chay tren PC: bien dich CHINH firmware that (uart_protocol,
//  device_fsm, voice_input) va noi kenh UART qua stdin/stdout.
//
//    make sim                       -> sinh ra ./sim
//    python tools/run_esp.py --sim  -> tu dong goi ./sim thay cho ESP32 that
//
//  stdin nhan 2 loai du lieu:
//    * Khung tin giao thuc ($..*xx\n)  -> coi nhu may tinh gui xuong ESP32.
//    * Dong dieu khien bat dau bang '!' -> gia lap dau vao phan cung:
//        !CUP 1|0        dat coc vao / nha coc ra (cam bien)
//        !BTN <1..5>     nhan nut muc (tu nhan den tha, 80 ms)
//        !BTN 0          nhan nut DUNG
//        !BTN <1..5> L   giu lau nut muc (1.2 s) -> chi doi muc, khong rot
//        !VOICE <ma>     gia lap lenh giong noi (0..4 = muc 1..5, 5 = rot, 6 = dung)
//        !PIN <so> <0|1> dat truc tiep muc mot chan GPIO (de test)
//
//  Loi ich: chay thu toan bo luat dieu khien (cam bien -> nut/mic -> relay,
//  watchdog, cat bom khi nha coc) ma khong can nap phan cung.
// =====================================================================
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

#include "config.h"
#include "device_fsm.h"
#include "host/host_board.h"

using namespace cupfiller;

static std::atomic<bool> g_run(true);
static std::atomic<int> g_voice(-2);       // -2 = khong co lenh; -1 = VOICE_NONE
static std::atomic<long> g_release_pin(-1);
static std::atomic<long> g_release_at_ms(-1);

static long now_ms() { return (long)host::millis_now(); }

// ---------------------------------------------------------------------------
static void handle_control(const std::string &line) {
  // line khong chua '!' dau va khong chua '\n'
  std::string s = line;
  size_t sp = s.find(' ');
  const std::string cmd = (sp == std::string::npos) ? s : s.substr(0, sp);
  const std::string rest = (sp == std::string::npos) ? std::string() : s.substr(sp + 1);
  const int a = rest.empty() ? 0 : atoi(rest.c_str());

  if (cmd == "CUP") {
    host::set_pin(PIN_CUP_SENSOR, a ? CUP_ACTIVE_LEVEL : (CUP_ACTIVE_LEVEL ? 0 : 1));
  } else if (cmd == "BTN") {
    const bool long_press = rest.find('L') != std::string::npos;
    int idx = atoi(rest.c_str());
    const uint8_t pin = (idx >= 1 && idx <= N_LEVEL_BUTTONS) ? kLevelPins[idx - 1]
                                                             : (uint8_t)PIN_BTN_STOP;
    host::set_pin(pin, BTN_ACTIVE_LEVEL);
    g_release_pin = pin;
    g_release_at_ms = now_ms() + (long_press ? (long)(BTN_LONG_PRESS_MS + 300) : 80);
  } else if (cmd == "VOICE") {
    g_voice = a;
  } else if (cmd == "PIN") {
    size_t sp2 = rest.find(' ');
    if (sp2 != std::string::npos) {
      const int pin = atoi(rest.substr(0, sp2).c_str());
      const int lvl = atoi(rest.substr(sp2 + 1).c_str());
      host::set_pin(pin, lvl);
    }
  } else if (cmd == "QUIT") {
    g_run = false;
  }
}

// ---------------------------------------------------------------------------
static void stdin_thread() {
  std::string line;
  bool at_line_start = true;
  bool in_control = false;
  while (g_run) {
    char c;
    if (!std::cin.get(c)) break;
    if (at_line_start && c == '!') {
      in_control = true;
      line.clear();
      at_line_start = false;
      continue;
    }
    if (in_control) {
      if (c == '\n' || c == '\r') {
        handle_control(line);
        in_control = false;
        line.clear();
        at_line_start = true;
      } else {
        line += c;
      }
      continue;
    }
    host::link_inject(std::string(1, c));  // du lieu giao thuc that
    at_line_start = (c == '\n');
  }
  g_run = false;
}

// ---------------------------------------------------------------------------
int main() {
  host::reset_board();
  host::use_real_clock();

  DeviceFSM fsm;
  fsm.begin();

  std::thread rd(stdin_thread);

  while (g_run) {
    const int v = g_voice.exchange(-2);
    if (v != -2 && v >= 0) fsm.inject_voice(v);

    const long rp = g_release_pin;
    if (rp >= 0 && now_ms() >= g_release_at_ms) {
      host::set_pin((int)rp, BTN_ACTIVE_LEVEL ? 0 : 1);
      g_release_pin = -1;
    }

    fsm.loop();

    const std::string out = host::link_drain();
    if (!out.empty()) {
      fwrite(out.data(), 1, out.size(), stdout);
      fflush(stdout);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }

  if (rd.joinable()) rd.detach();
  return 0;
}
