// ===========================================================================
//  pi_protocol.h - PHẦN GIAO THỨC MỞ RỘNG CHO BẢN RASPBERRY PI
//
//  Khung truyền giữ đúng bản cũ:
//      AA 55 | LEN | SEQ | MSG | PAYLOAD | CRC16-CCITT-FALSE (LE)
//
//  Chỉ thêm các mã gói chưa dùng trong protocol.h cũ:
//    ESP32 -> Pi:
//      0x10 INPUT_STATE     payload ĐÚNG 5 byte, thứ tự yêu cầu:
//                           [0] cảm biến
//                           [1] nút START GPIO26
//                           [2] nút mức 1 GPIO32 (30%)
//                           [3] nút mức 2 GPIO33 (60%)
//                           [4] nút mức 3 GPIO25 (90%)
//                           Mỗi byte chỉ có 0x00 hoặc 0x01. 1 = cảm biến tác động
//                           / nút đang được nhấn (đã đổi từ active-LOW sang logic).
//      0x11 LEVEL_SELECTED  index:u8, percent:u8, source:u8
//      0x12 START_REQUEST   selected_index:u8 (0xFF = chưa chọn), percent:u8
//      0x13 VOICE_REQUEST   selected_index=0xFF, percent=0; START được nhấn
//                           trước mọi nút mức, host sẽ hỏi mức bằng giọng nói
//    Pi -> ESP32:
//      0x8E SPEAKER_AUDIO   seq8:u8, flags:u8, n:u16, PCM mono int16 LE 16 kHz
//      0x8F AUDIO_CTRL      mic_enable:u8, mic_continuous:u8,
//                           speaker_enable:u8, volume_pct:u8
//
//  Mic ESP32 -> Pi dùng lại AUDIO_CHUNK (0x0A) của giao thức cũ. Các lệnh bơm
//  CUP_OK / START_FILL / PUMP_SET / STOP_FILL cũng giữ nguyên.
// ===========================================================================
#ifndef CUPFILLER_PI_PROTOCOL_H
#define CUPFILLER_PI_PROTOCOL_H

#include "../esp32_cup_filler/protocol.h"

namespace piproto {

enum Msg : uint8_t {
  INPUT_STATE    = 0x10,
  LEVEL_SELECTED = 0x11,
  START_REQUEST  = 0x12,
  VOICE_REQUEST  = 0x13,

  SPEAKER_AUDIO  = 0x8E,
  AUDIO_CTRL     = 0x8F,
};

// Ba nút mức trong INPUT_STATE / LEVEL_SELECTED.
static constexpr uint8_t N_LEVELS = 3;
static constexpr uint8_t NO_LEVEL = 0xFF;

// ---------------------------------------------------------------- encoders --
inline size_t encInputState(proto::Writer &w, uint8_t seq, bool sensor,
                            bool start, const bool levels[N_LEVELS]) {
  w.begin(INPUT_STATE, seq);
  w.u8(sensor ? 1 : 0);                         // [0] cảm biến
  w.u8(start ? 1 : 0);                          // [1] START GPIO26
  for (uint8_t i = 0; i < N_LEVELS; i++) {      // [2..4] nút chọn mức
    w.u8(levels[i] ? 1 : 0);
  }
  return w.end();
}

inline size_t encLevelSelected(proto::Writer &w, uint8_t seq, uint8_t index,
                               uint8_t percent, uint8_t source) {
  w.begin(LEVEL_SELECTED, seq);
  w.u8(index);
  w.u8(percent);
  w.u8(source);
  return w.end();
}

inline size_t encStartRequest(proto::Writer &w, uint8_t seq,
                              uint8_t selectedIndex, uint8_t percent) {
  w.begin(START_REQUEST, seq);
  w.u8(selectedIndex);
  w.u8(percent);
  return w.end();
}

inline size_t encVoiceRequest(proto::Writer &w, uint8_t seq) {
  w.begin(VOICE_REQUEST, seq);
  w.u8(NO_LEVEL);  // chưa có nút mức vật lý nào được chọn
  w.u8(0);
  return w.end();
}

// ---------------------------------------------------------------- decoders --
struct SpeakerAudioView {
  uint8_t seq8 = 0;
  uint8_t flags = 0;
  uint16_t n = 0;
  const uint8_t *pcmLe = NULL;                  // trỏ vào payload gốc, không copy
};

inline bool parseSpeakerAudio(const uint8_t *p, uint8_t len,
                              SpeakerAudioView &out) {
  proto::Reader r(p, len);
  out.seq8 = r.u8();
  out.flags = r.u8();
  out.n = r.u16();
  if (!r.ok()) return false;
  if ((uint32_t)out.n * 2u > (uint32_t)r.remaining()) return false;
  out.pcmLe = r.ptr();
  return true;
}

inline int16_t speakerSample(const SpeakerAudioView &v, uint16_t i) {
  if (i >= v.n || v.pcmLe == NULL) return 0;
  const uint8_t *b = v.pcmLe + ((size_t)i * 2u);
  return (int16_t)((uint16_t)b[0] | ((uint16_t)b[1] << 8));
}

struct AudioCtrl {
  bool micEnable = true;
  bool micContinuous = false;
  bool speakerEnable = true;
  uint8_t volumePct = 80;
};

inline bool parseAudioCtrl(const uint8_t *p, uint8_t len, AudioCtrl &out) {
  proto::Reader r(p, len);
  out.micEnable = (r.u8() != 0);
  out.micContinuous = (r.u8() != 0);
  out.speakerEnable = (r.u8() != 0);
  out.volumePct = r.u8();
  if (out.volumePct > 100) out.volumePct = 100;
  return r.ok();
}

}  // namespace piproto

#endif  // CUPFILLER_PI_PROTOCOL_H
