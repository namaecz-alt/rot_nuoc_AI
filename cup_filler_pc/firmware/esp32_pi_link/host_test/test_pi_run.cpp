// Chạy THẬT logic esp32_pi_link.ino trên máy ảo Arduino của repo.
// Từ thư mục gốc repo:
//   g++ -std=c++11 -O0 -Wall -Wextra
//       -I cup_filler_pc/firmware/esp32_pi_link/host_test/stub
//       -I cup_filler_pc/firmware/host_test/arduino_stub
//       -I cup_filler_pc/firmware/esp32_pi_link
//       -I cup_filler_pc/firmware/esp32_cup_filler
//       -o /tmp/test_pi_run
//       cup_filler_pc/firmware/esp32_pi_link/host_test/test_pi_run.cpp
//       cup_filler_pc/firmware/host_test/arduino_stub/instances.cpp
//   /tmp/test_pi_run
#include <stdio.h>
#include <string>
#include <vector>

#include "../esp32_pi_link.ino"

static int g_ok = 0;
static int g_fail = 0;

static void check(bool cond, const char *label) {
  if (cond) {
    g_ok++;
    printf("  OK  %s\n", label);
  } else {
    g_fail++;
    printf("  FAIL %s\n", label);
  }
}

static void runLoops(int n) {
  for (int i = 0; i < n; i++) loop();
}

static std::vector<proto::Frame> takeFrames() {
  std::string bytes = stub_uart_take();
  proto::Decoder dec;
  std::vector<proto::Frame> out;
  proto::Frame f;
  for (size_t i = 0; i < bytes.size(); i++) {
    if (dec.push((uint8_t)bytes[i])) {
      while (dec.pop(f)) out.push_back(f);
    }
  }
  while (dec.pop(f)) out.push_back(f);
  return out;
}

static bool lastInputIs(const std::vector<proto::Frame> &fs, const uint8_t want[5]) {
  for (int i = (int)fs.size() - 1; i >= 0; i--) {
    if (fs[i].msg == piproto::INPUT_STATE) {
      return fs[i].len == 5 && memcmp(fs[i].data, want, 5) == 0;
    }
  }
  return false;
}

static bool hasMsg(const std::vector<proto::Frame> &fs, uint8_t msg) {
  for (size_t i = 0; i < fs.size(); i++) {
    if (fs[i].msg == msg) return true;
  }
  return false;
}

static void sendCupOk() {
  uint8_t buf[96];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::CUP_OK, 1);
  w.u16(350);
  w.u16(250);
  w.u16(1000);
  w.u16(300);
  w.u8(95);
  w.u8(0);
  w.text("pi-test", 16);
  size_t n = w.end();
  stub_uart_inject(std::string((const char *)buf, n));
}

static void sendStartFill() {
  uint8_t buf[64];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::START_FILL, 2);
  w.u16(180);
  w.u8(proto::MODE_PC_CLOSED_LOOP);
  w.u8(proto::SRC_BUTTON);
  w.u16(60000);
  size_t n = w.end();
  stub_uart_inject(std::string((const char *)buf, n));
}

int main() {
  stub_reset();
  setup();
  runLoops(400);
  takeFrames();

  // START GPIO26 đang nhấn => [sensor, START, level32, level33, level25]
  stub_set_pin(26, LOW);
  runLoops(40);
  {
    const uint8_t want[5] = {0, 1, 0, 0, 0};
    check(lastInputIs(takeFrames(), want), "START GPIO26 -> [0,1,0,0,0]");
  }
  stub_set_pin(26, HIGH);
  runLoops(40);
  takeFrames();

  stub_set_pin(32, LOW);
  stub_set_pin(25, LOW);
  runLoops(40);
  {
    const uint8_t want[5] = {0, 0, 1, 0, 1};
    check(lastInputIs(takeFrames(), want), "Level GPIO32+25 -> [0,0,1,0,1]");
  }
  stub_set_pin(32, HIGH);
  stub_set_pin(25, HIGH);
  runLoops(40);
  takeFrames();

  stub_set_pin(34, LOW);  // cảm biến active LOW
  runLoops(120);
  {
    std::vector<proto::Frame> fs = takeFrames();
    const uint8_t want[5] = {1, 0, 0, 0, 0};
    check(lastInputIs(fs, want), "Sensor GPIO34 -> [1,0,0,0,0]");
    check(hasMsg(fs, proto::CUP_PLACED), "Sensor -> CUP_PLACED");
  }
  check(stub_pin(27) == LOW, "Relay GPIO27 van OFF truoc CUP_OK/START_FILL");

  sendCupOk();
  runLoops(10);
  takeFrames();

  stub_set_pin(26, LOW);
  runLoops(40);
  {
    std::vector<proto::Frame> fs = takeFrames();
    check(hasMsg(fs, piproto::VOICE_REQUEST),
          "START truoc nut muc -> VOICE_REQUEST");
    check(!hasMsg(fs, piproto::START_REQUEST),
          "START truoc nut muc khong gui START_REQUEST");
  }
  stub_set_pin(26, HIGH);
  runLoops(40);
  takeFrames();

  stub_set_pin(33, LOW);
  runLoops(40);
  check(hasMsg(takeFrames(), piproto::LEVEL_SELECTED),
        "GPIO33 -> LEVEL_SELECTED sau CUP_OK");
  stub_set_pin(33, HIGH);
  runLoops(40);
  takeFrames();

  stub_set_pin(26, LOW);
  runLoops(40);
  check(hasMsg(takeFrames(), piproto::START_REQUEST),
        "START -> START_REQUEST");
  check(stub_pin(27) == LOW,
        "START khong tu dong relay khi Pi dieu khien bom");
  stub_set_pin(26, HIGH);
  runLoops(40);
  takeFrames();

  sendStartFill();
  runLoops(10);
  check(stub_pin(27) == HIGH, "START_FILL tu Pi -> relay GPIO27 HIGH");

  stub_set_pin(34, HIGH);
  runLoops(120);
  check(stub_pin(27) == LOW, "Mat cam bien -> relay OFF ngay");

  printf("KET QUA PI RUN: %d dung / %d sai\n", g_ok, g_fail);
  return g_fail ? 1 : 0;
}
