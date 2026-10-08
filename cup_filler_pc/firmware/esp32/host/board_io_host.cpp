// board_io + link_io cho che do chay tren PC (khong co Arduino).
#include <chrono>
#include <deque>
#include <map>
#include <mutex>
#include <string>
#include <thread>

#include "board_io.h"
#include "host/host_board.h"
#include "link_io.h"

namespace cupfiller {
namespace host {

namespace {
bool g_manual_clock = false;
uint32_t g_ms = 0;
std::map<int, int> g_level;
std::map<int, uint32_t> g_writes;
std::mutex g_mtx;
std::deque<char> g_in;   // may tinh -> ESP32
std::deque<char> g_out;  // ESP32 -> may tinh

uint32_t real_ms() {
  using namespace std::chrono;
  static const steady_clock::time_point t0 = steady_clock::now();
  return (uint32_t)duration_cast<milliseconds>(steady_clock::now() - t0).count();
}
}  // namespace

void use_real_clock() { g_manual_clock = false; }
void set_millis(uint32_t ms) {
  g_manual_clock = true;
  g_ms = ms;
}
void advance_millis(uint32_t d_ms) {
  g_manual_clock = true;
  g_ms += d_ms;
}
uint32_t millis_now() { return g_manual_clock ? g_ms : real_ms(); }

void reset_board() {
  std::lock_guard<std::mutex> lk(g_mtx);
  g_level.clear();
  g_writes.clear();
  g_in.clear();
  g_out.clear();
  g_ms = 0;
}

void set_pin(int pin, int level) {
  std::lock_guard<std::mutex> lk(g_mtx);
  g_level[pin] = level ? 1 : 0;
}

int get_pin(int pin) {
  std::lock_guard<std::mutex> lk(g_mtx);
  auto it = g_level.find(pin);
  return it == g_level.end() ? 1 : it->second;
}

uint32_t writes_on(int pin) {
  std::lock_guard<std::mutex> lk(g_mtx);
  auto it = g_writes.find(pin);
  return it == g_writes.end() ? 0u : it->second;
}

void clear_writes(int pin) {
  std::lock_guard<std::mutex> lk(g_mtx);
  g_writes[pin] = 0;
}

void link_inject(const std::string &s) {
  std::lock_guard<std::mutex> lk(g_mtx);
  for (size_t i = 0; i < s.size(); ++i) g_in.push_back(s[i]);
}

std::string link_drain() {
  std::lock_guard<std::mutex> lk(g_mtx);
  std::string s(g_out.begin(), g_out.end());
  g_out.clear();
  return s;
}

size_t link_out_size() {
  std::lock_guard<std::mutex> lk(g_mtx);
  return g_out.size();
}

}  // namespace host

// ---------------------------------------------------------------------------
// board_io.h
uint32_t io_millis() { return host::millis_now(); }

void io_pin_input_pullup(uint8_t pin) {
  std::lock_guard<std::mutex> lk(host::g_mtx);
  // INPUT_PULLUP: khong co gi keo chan -> muc mac dinh = 1 (nhu pull-up that)
  if (host::g_level.find(pin) == host::g_level.end()) host::g_level[pin] = 1;
}

void io_pin_output(uint8_t pin) {
  std::lock_guard<std::mutex> lk(host::g_mtx);
  host::g_level[pin] = 0;  // giong board_io_arduino: bat dau o muc THAP
}

int io_read(uint8_t pin) { return host::get_pin(pin); }

void io_write(uint8_t pin, int level) {
  const int v = level ? 1 : 0;
  std::lock_guard<std::mutex> lk(host::g_mtx);
  auto it = host::g_level.find(pin);
  if (it == host::g_level.end() || it->second != v) host::g_writes[pin] += 1;
  host::g_level[pin] = v;
}

void io_delay_ms(uint32_t ms) {
  if (host::g_manual_clock) {
    host::g_ms += ms;
    return;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

// ---------------------------------------------------------------------------
// link_io.h
void link_write(const uint8_t *data, size_t n) {
  std::lock_guard<std::mutex> lk(host::g_mtx);
  for (size_t i = 0; i < n; ++i) host::g_out.push_back((char)data[i]);
}

size_t link_read(uint8_t *buf, size_t max_n) {
  std::lock_guard<std::mutex> lk(host::g_mtx);
  size_t k = 0;
  while (k < max_n && !host::g_in.empty()) {
    buf[k++] = (uint8_t)host::g_in.front();
    host::g_in.pop_front();
  }
  return k;
}

}  // namespace cupfiller
