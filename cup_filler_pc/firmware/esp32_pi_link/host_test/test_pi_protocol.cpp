// Test giao thức Pi trên máy tính (không cần ESP32):
//   g++ -std=c++11 -O2 -Wall -I cup_filler_pc/firmware/esp32_cup_filler
//       -o /tmp/test_pi_protocol
//       cup_filler_pc/firmware/esp32_pi_link/host_test/test_pi_protocol.cpp
//   /tmp/test_pi_protocol
#include <stdio.h>
#include <string.h>

#include "../pi_protocol.h"

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

static void hex(const uint8_t *b, size_t n) {
  for (size_t i = 0; i < n; i++) printf("%02X%s", b[i], i + 1 == n ? "" : " ");
  printf("\n");
}

int main() {
  // INPUT_STATE: sensor=1, START=0, levels=[1,0,1]
  uint8_t buf[64];
  proto::Writer w(buf, sizeof(buf));
  const bool levels[3] = {true, false, true};
  size_t n = piproto::encInputState(w, 0x2A, true, false, levels);
  check(n == 12, "INPUT_STATE frame = 12 byte");
  printf("INPUT_STATE seq=0x2A, payload=[01 00 01 00 01]:\n  ");
  hex(buf, n);

  proto::Decoder dec;
  proto::Frame f;
  bool got = false;
  for (size_t i = 0; i < n; i++) {
    if (dec.push(buf[i])) {
      while (dec.pop(f)) got = true;
    }
  }
  check(got && f.msg == piproto::INPUT_STATE && f.seq == 0x2A,
        "Decoder nhan dung MSG/SEQ INPUT_STATE");
  check(f.len == 5 && f.data[0] == 1 && f.data[1] == 0 && f.data[2] == 1 &&
            f.data[3] == 0 && f.data[4] == 1,
        "Thu tu payload: sensor, START, level1, level2, level3");

  // START_REQUEST + LEVEL_SELECTED mã hoá được và giải mã lại bằng Decoder cũ.
  uint8_t buf2[64];
  proto::Writer w2(buf2, sizeof(buf2));
  size_t n2 = piproto::encStartRequest(w2, 7, 2, 90);
  check(n2 > 0 && buf2[4] == piproto::START_REQUEST, "START_REQUEST msg=0x12");

  // SPEAKER_AUDIO payload do Pi tạo: seq8=9, flags=START, 3 mẫu [-2,0,300].
  const uint8_t spk[] = {9, 1, 3, 0, 0xFE, 0xFF, 0x00, 0x00, 0x2C, 0x01};
  piproto::SpeakerAudioView v;
  check(piproto::parseSpeakerAudio(spk, sizeof(spk), v),
        "parse SPEAKER_AUDIO header");
  check(v.seq8 == 9 && v.n == 3 && piproto::speakerSample(v, 0) == -2 &&
            piproto::speakerSample(v, 1) == 0 &&
            piproto::speakerSample(v, 2) == 300,
        "PCM int16 little-endian cua loa dung");
  check(!piproto::parseSpeakerAudio(spk, 5, v),
        "payload loa cut bi tu choi");

  // AUDIO_CTRL.
  const uint8_t ctrl[] = {1, 1, 0, 120};
  piproto::AudioCtrl ac;
  check(piproto::parseAudioCtrl(ctrl, sizeof(ctrl), ac) && ac.micEnable &&
            ac.micContinuous && !ac.speakerEnable && ac.volumePct == 100,
        "parse AUDIO_CTRL + clamp volume 100");

  printf("KET QUA: %d dung / %d sai\n", g_ok, g_fail);
  return g_fail == 0 ? 0 : 1;
}
