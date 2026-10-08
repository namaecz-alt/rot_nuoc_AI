#include "voice_input.h"

#include <string.h>

#include "board_io.h"
#include "config.h"

// Che do 2 (UART) can Arduino HardwareSerial -> chi bien dich tren ESP32 that.
#if defined(ARDUINO) && (VOICE_MODE == 2)
#include <Arduino.h>
#define VOICE_NEED_UART 1
#if VOICE_UART_NUM == 2
static HardwareSerial VoiceSerial(2);
#elif VOICE_UART_NUM == 1
static HardwareSerial VoiceSerial(1);
#else
static HardwareSerial VoiceSerial(0);
#endif
#endif

namespace cupfiller {

// ---------------------------------------------------------------------------
// Che do 1: moi cau lenh giong noi = mot chan GPIO (tich cuc THAP, co pull-up)
// ---------------------------------------------------------------------------
#if VOICE_MODE == 1
namespace {
struct VoicePin {
  int raw;
  uint32_t change_ms;
  bool stable_active;
};
VoicePin g_vp[N_VOICE_PINS];
const uint32_t kVoiceDebounceMs = 40;
}  // namespace

void voice_begin() {
  for (int i = 0; i < N_VOICE_PINS; ++i) {
    io_pin_input_pullup(kVoicePins[i]);
    g_vp[i].raw = 1;
    g_vp[i].change_ms = 0;
    g_vp[i].stable_active = false;
  }
}

int voice_poll(uint32_t now_ms) {
  for (int i = 0; i < N_VOICE_PINS; ++i) {
    const int lvl = io_read(kVoicePins[i]);
    if (lvl != g_vp[i].raw) {
      g_vp[i].raw = lvl;
      g_vp[i].change_ms = now_ms;
      continue;
    }
    const bool active = (lvl == 0);  // tich cuc THAP
    if (active == g_vp[i].stable_active) continue;
    if ((uint32_t)(now_ms - g_vp[i].change_ms) < kVoiceDebounceMs) continue;
    g_vp[i].stable_active = active;
    if (active) {
      // chan 0..4 = muc 1..5, chan 5 = "rot nuoc" (bat dau)
      return (i < 5) ? (VOICE_LEVEL_1 + i) : (int)VOICE_START;
    }
  }
  return VOICE_NONE;
}
const char *voice_backend_name() { return "VOICE_PINS"; }

// ---------------------------------------------------------------------------
// Che do 2: module giong noi truyen ma lenh qua UART (vi du SU-03T, LD3320)
// ---------------------------------------------------------------------------
#elif defined(VOICE_NEED_UART)
namespace {
struct VoiceCode {
  const char *text;
  int cmd;
};
// Sua bang nay theo cach ban cau hinh module (TuyaAI/SU-03T gui chuoi ASCII).
const VoiceCode kVoiceCodes[] = {
    {"A1", VOICE_LEVEL_1}, {"A2", VOICE_LEVEL_2}, {"A3", VOICE_LEVEL_3},
    {"A4", VOICE_LEVEL_4}, {"A5", VOICE_LEVEL_5},
    {"B1", VOICE_START},   {"B2", VOICE_STOP},
};
char g_line[24];
size_t g_n = 0;
uint32_t g_last = 0;
}  // namespace

void voice_begin() {
  VoiceSerial.begin(9600, SERIAL_8N1, PIN_VOICE_RX, PIN_VOICE_TX);
  g_n = 0;
  g_last = 0;
}

int voice_poll(uint32_t now_ms) {
  while (VoiceSerial.available() > 0) {
    const char c = (char)VoiceSerial.read();
    g_last = now_ms;
    if (c == '\r' || c == '\n') {
      g_line[g_n] = '\0';
      const char *s = g_line;
      while (*s == ' ') ++s;
      for (size_t i = 0; i < sizeof(kVoiceCodes) / sizeof(kVoiceCodes[0]); ++i) {
        if (strcmp(s, kVoiceCodes[i].text) == 0) {
          g_n = 0;
          return kVoiceCodes[i].cmd;
        }
      }
      g_n = 0;
      continue;
    }
    if (g_n + 1 < sizeof(g_line)) g_line[g_n++] = c;
  }
  if (g_n && now_ms - g_last > 60) g_n = 0;  // xoa dong rac bi dut
  return VOICE_NONE;
}
const char *voice_backend_name() { return "VOICE_UART"; }

// ---------------------------------------------------------------------------
// Che do 3: ESP-SR (file rieng) / che do 0: khong dung
// ---------------------------------------------------------------------------
#elif VOICE_MODE == 3
// voice_begin()/voice_poll() nam trong voice_input_espsr.cpp (can -DUSE_ESP_SR=1)
const char *voice_backend_name() { return "ESP_SR"; }
#if !defined(USE_ESP_SR)
#warning "VOICE_MODE=3 nhung chua bat USE_ESP_SR -> mic se khong hoat dong"
void voice_begin() {}
int voice_poll(uint32_t) { return VOICE_NONE; }
#endif

#else
void voice_begin() {}
int voice_poll(uint32_t) { return VOICE_NONE; }
const char *voice_backend_name() { return "VOICE_OFF"; }
#endif

}  // namespace cupfiller
