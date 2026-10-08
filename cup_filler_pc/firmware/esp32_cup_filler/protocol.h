// ===========================================================================
//  protocol.h - GIAO THỨC UART ESP32 <-> MÁY TÍNH  (bản C++, dùng cho firmware)
//
//  Đặc tả đầy đủ: docs/PROTOCOL.md.  Bản Python: cupfiller/protocol.py.
//  File này CỐ Ý không phụ thuộc Arduino (chỉ <stdint.h>/<string.h>) để có thể
//  biên dịch thử trên máy tính: firmware/host_test/test_protocol.cpp
//  -> tools/test_comms.py kiểm tra hai bản khớp byte với nhau.
//
//  Khung truyền:
//      AA 55 | LEN | SEQ | MSG | PAYLOAD (LEN-2 byte) | CRC16 (thấp, cao)
//      LEN = 2 + len(payload);  CRC16-CCITT-FALSE tính trên LEN,SEQ,MSG,PAYLOAD
// ===========================================================================
#ifndef CUPFILLER_PROTOCOL_H
#define CUPFILLER_PROTOCOL_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace proto {

constexpr uint8_t  SYNC1         = 0xAA;
constexpr uint8_t  SYNC2         = 0x55;
constexpr uint8_t  PROTO_VERSION = 1;
constexpr uint8_t  MAX_PAYLOAD   = 240;
constexpr uint8_t  CUP_OK_LABEL_LEN = 16;
constexpr uint8_t  MAX_TEXT      = 48;

// ---- mã gói tin ----------------------------------------------------------
enum Msg : uint8_t {
  // ESP32 -> PC
  HELLO           = 0x01,
  CUP_PLACED      = 0x02,
  CUP_REMOVED     = 0x03,
  PRESET_SELECTED = 0x04,
  FILL_STARTED    = 0x05,
  FILL_PROGRESS   = 0x06,
  FILL_DONE       = 0x07,
  STATUS          = 0x08,
  VOICE_EVENT     = 0x09,
  AUDIO_CHUNK     = 0x0A,
  BUTTON_EVENT    = 0x0B,
  ERROR           = 0x0C,
  ACK             = 0x0D,
  PONG            = 0x0E,
  LOG             = 0x0F,
  // PC -> ESP32
  HELLO_ACK  = 0x81,
  CUP_OK     = 0x82,
  CUP_REJECT = 0x83,
  SET_PRESET = 0x84,
  START_FILL = 0x85,
  STOP_FILL  = 0x86,
  SET_MODE   = 0x87,
  PUMP_SET   = 0x88,
  SET_PARAMS = 0x89,
  CMD        = 0x8A,
  PING       = 0x8B,
  ACK_PC     = 0x8C,
  ERROR_PC   = 0x8D,
};

// ---- trạng thái máy / chế độ --------------------------------------------
enum State : uint8_t {
  ST_BOOT = 0, ST_IDLE = 1, ST_WAIT_PC = 2, ST_READY = 3,
  ST_POURING = 4, ST_DONE = 5, ST_FAULT = 6, ST_MANUAL = 7,
};

enum Mode : uint8_t {
  MODE_ESP_OPEN_LOOP = 0,   // ESP tự đong theo ml/s đã hiệu chuẩn
  MODE_PC_CLOSED_LOOP = 1,  // PC nhìn vạch nước, điều khiển relay bằng PUMP_SET
};

enum Source : uint8_t { SRC_BUTTON = 0, SRC_VOICE = 1, SRC_PC = 2, SRC_AUTO = 3 };

enum RejectReason : uint8_t {
  REJ_NOT_FOUND = 0, REJ_NOT_A_CUP = 1, REJ_TOO_SMALL = 2,
  REJ_BUSY = 3, REJ_CAMERA_ERROR = 4, REJ_UNKNOWN = 5,
};

enum StopReason : uint8_t {
  STOP_NORMAL = 0, STOP_PC_REQUEST = 1, STOP_CUP_REMOVED = 2, STOP_TIMEOUT = 3,
  STOP_LINK_LOST = 4, STOP_BUTTON_ESTOP = 5, STOP_OVER_VOLUME = 6, STOP_SENSOR_FAULT = 7,
};

enum Cmd : uint8_t {
  CMD_ARM = 0, CMD_DISARM = 1, CMD_TARE = 2, CMD_RESET_STATE = 3,
  CMD_SELF_TEST = 4, CMD_SAVE_CAL = 5,
};

enum BtnEvent : uint8_t { BTN_SHORT = 0, BTN_LONG = 1, BTN_ESTOP = 2 };

enum VoiceKind : uint8_t {
  VOICE_UNKNOWN = 0, VOICE_PRESET_WORD = 1, VOICE_CLAPS = 2, VOICE_TEXT = 3,
};

// bit trong STATUS.flags
constexpr uint8_t FL_CUP_PRESENT  = 1 << 0;
constexpr uint8_t FL_PC_CONFIRMED = 1 << 1;
constexpr uint8_t FL_PUMPING      = 1 << 2;
constexpr uint8_t FL_VOICE_ACTIVE = 1 << 3;
constexpr uint8_t FL_LINK_OK      = 1 << 4;
constexpr uint8_t FL_MANUAL       = 1 << 5;

// cờ AUDIO_CHUNK.flags
constexpr uint8_t AU_START = 1 << 0;
constexpr uint8_t AU_END   = 1 << 1;

// năng lực
constexpr uint8_t ESP_CAP_BUTTONS  = 1 << 0;
constexpr uint8_t ESP_CAP_VOICE    = 1 << 1;
constexpr uint8_t ESP_CAP_US_SENSOR = 1 << 2;
constexpr uint8_t ESP_CAP_FLOW_CAL = 1 << 3;

// =========================================================================
uint16_t crc16_ccitt(const uint8_t *data, size_t n, uint16_t crc = 0xFFFF) {
  for (size_t i = 0; i < n; i++) {
    crc ^= (uint16_t)((uint16_t)data[i] << 8);
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 0x8000) crc = (uint16_t)((crc << 1) ^ 0x1021);
      else              crc = (uint16_t)(crc << 1);
    }
  }
  return crc;
}

// =========================================================================
//  Writer: đóng gói frame vào bộ đệm do người gọi cấp phát (không cấp phát động)
// =========================================================================
class Writer {
 public:
  Writer(uint8_t *buf, size_t cap) : _buf(buf), _cap(cap), _len(0), _ok(cap >= 5) {}

  bool begin(uint8_t msg, uint8_t seq) {          // ghi sync + chừa chỗ LEN/SEQ/MSG
    _len = 0;
    _ok = (_cap >= 6);
    if (!_ok) return false;
    _buf[_len++] = SYNC1;
    _buf[_len++] = SYNC2;
    _buf[_len++] = 0;                             // LEN lấp sau
    _buf[_len++] = seq;
    _buf[_len++] = msg;
    return true;
  }
  void u8(uint8_t v)   { if (_put(1)) _buf[_len++] = v; }
  void u16(uint16_t v) { if (_put(2)) { _buf[_len++] = (uint8_t)(v & 0xFF); _buf[_len++] = (uint8_t)(v >> 8); } }
  void i16(int16_t v)  { u16((uint16_t)v); }
  void u32(uint32_t v) {
    if (_put(4)) {
      _buf[_len++] = (uint8_t)(v & 0xFF); _buf[_len++] = (uint8_t)((v >> 8) & 0xFF);
      _buf[_len++] = (uint8_t)((v >> 16) & 0xFF); _buf[_len++] = (uint8_t)((v >> 24) & 0xFF);
    }
  }
  void raw(const void *p, size_t n) { if (_put(n)) { memcpy(_buf + _len, p, n); _len += n; } }
  void text(const char *s, size_t field) {              // chuỗi + đệm 0 cho đủ field
    size_t n = s ? strlen(s) : 0;
    if (n > field - 1) n = field - 1;
    if (_put(field)) {
      if (n) memcpy(_buf + _len, s, n);
      memset(_buf + _len + n, 0, field - n);
      _len += field;
    }
  }

  size_t end() {                                        // ghi LEN + CRC, trả về tổng số byte
    if (!_ok) return 0;
    uint8_t plen = (uint8_t)(_len - 5);                 // trừ sync(2) + LEN + SEQ + MSG
    _buf[2] = (uint8_t)(2 + plen);                      // LEN = SEQ + MSG + payload
    // CRC phủ LEN,SEQ,MSG,PAYLOAD  =  LEN + 1 byte
    uint16_t crc = crc16_ccitt(_buf + 2, (size_t)_buf[2] + 1);
    if (_len + 2 > _cap) { _ok = false; return 0; }
    _buf[_len++] = (uint8_t)(crc & 0xFF);
    _buf[_len++] = (uint8_t)(crc >> 8);
    return _len;
  }
  size_t length() const { return _len; }
  bool   ok() const { return _ok; }

 private:
  bool _put(size_t n) { _ok = _ok && (_len + n + 2 <= _cap); return _ok; }
  uint8_t *_buf;
  size_t   _cap, _len;
  bool     _ok;
};

// =========================================================================
//  Reader: đọc payload theo thứ tự (little-endian)
// =========================================================================
class Reader {
 public:
  Reader(const uint8_t *p, uint8_t n) : _p(p), _n(n), _i(0) {}
  bool ok() const { return _i <= _n; }
  uint8_t  u8()  { if (_i + 1 > _n) { _i = _n + 1; return 0; } return _p[_i++]; }
  uint16_t u16() { if (_i + 2 > _n) { _i = _n + 1; return 0; } uint16_t v = (uint16_t)(_p[_i] | (_p[_i + 1] << 8)); _i += 2; return v; }
  int16_t  i16() { return (int16_t)u16(); }
  uint32_t u32() {
    if (_i + 4 > _n) { _i = _n + 1; return 0; }
    uint32_t v = (uint32_t)_p[_i] | ((uint32_t)_p[_i + 1] << 8) | ((uint32_t)_p[_i + 2] << 16) | ((uint32_t)_p[_i + 3] << 24);
    _i += 4; return v;
  }
  void raw(void *dst, size_t n) {
    if (_i + n > _n) { memset(dst, 0, n); _i = _n + 1; return; }
    memcpy(dst, _p + _i, n); _i += (uint8_t)n;
  }
  void text(char *dst, size_t field) {
    size_t avail = (_n > _i) ? (size_t)(_n - _i) : 0;
    size_t n = (field <= avail) ? field : avail;
    if (n) memcpy(dst, _p + _i, n);
    memset(dst + n, 0, field - n);
    dst[field - 1] = '\0';
    _i += (uint8_t)n;
  }
  uint8_t remaining() const { return (uint8_t)(_n > _i ? _n - _i : 0); }
  const uint8_t *ptr() const { return _p + _i; }

 private:
  const uint8_t *_p;
  uint8_t _n, _i;
};

// =========================================================================
//  Decoder: máy trạng thái nhận từng byte, đẩy ra frame hoàn chỉnh
// =========================================================================
struct Frame {
  uint8_t msg = 0;
  uint8_t seq = 0;
  uint8_t len = 0;
  uint8_t data[MAX_PAYLOAD] = {0};
};

class Decoder {
 public:
  static constexpr uint8_t  QUEUE  = 6;      // số frame chờ xử lý
  static constexpr uint16_t BUFSZ  = 640;    // đệm byte thô (>= 2 frame dài nhất)

  Decoder() { reset(); }

  void reset() {
    _len = 0; _head = _tail = _count = 0;
    n_frames = n_bad_crc = n_bad_len = n_dropped = 0;
  }

  // Nạp 1 byte; trả về true nếu vừa có frame mới trong hàng đợi.
  // Bộ đệm được DÒ LẠI SYNC sau mỗi lỗi (giống bản Python), nên gói cụt / CRC
  // hỏng / rác trên dây không làm mất các gói hợp lệ đi sau nó.
  bool push(uint8_t b) {
    if (_len >= BUFSZ) {                      // đệm đầy: bỏ nửa đầu để tự cứu
      uint16_t drop = BUFSZ / 2;
      memmove(_buf, _buf + drop, (size_t)(_len - drop));
      _len -= drop;
      n_dropped += drop;
    }
    _buf[_len++] = b;
    bool newFrame = false;
    for (;;) {
      // 1. dò sync
      int16_t i = findSync();
      if (i < 0) {
        uint8_t keep = (_len && _buf[_len - 1] == SYNC1) ? 1 : 0;
        if (_len > keep) { n_dropped += (uint16_t)(_len - keep); _len = keep; }
        return newFrame;
      }
      if (i > 0) {                            // bỏ rác trước sync
        n_dropped += (uint16_t)i;
        memmove(_buf, _buf + i, (size_t)(_len - i));
        _len = (uint16_t)(_len - i);
      }
      if (_len < 4) return newFrame;          // chưa đủ header
      uint8_t len = _buf[2];                  // LEN = SEQ + MSG + payload
      if (len < 2 || len > (uint8_t)(2 + MAX_PAYLOAD)) { n_bad_len++; dropFirst(); continue; }
      // bố cục: [0]=AA [1]=55 [2]=LEN [3]=SEQ [4]=MSG [5..]=payload, LEN = SEQ+MSG+payload
      uint16_t total = (uint16_t)(5 + len);   // sync(2) + LEN(1) + LEN byte + CRC(2)
      if (_len < total) return newFrame;      // chờ thêm byte
      uint16_t crc_rx = (uint16_t)(_buf[3 + len] | (_buf[4 + len] << 8));
      if (crc16_ccitt(_buf + 2, (size_t)len + 1) != crc_rx) {
        n_bad_crc++;
        dropFirst();                          // trượt 1 byte rồi dò lại (không mất gói sau)
        continue;
      }
      if (_count < QUEUE) {
        Frame &f = _q[_tail];
        f.msg = _buf[4];
        f.seq = _buf[3];
        f.len = (uint8_t)(len - 2);
        if (f.len) memcpy(f.data, _buf + 5, f.len);
        _tail = (uint8_t)((_tail + 1) % QUEUE);
        _count++;
        n_frames++;
        newFrame = true;
      } else {
        n_overflow++;                         // firmware chưa kịp lấy frame ra
      }
      memmove(_buf, _buf + total, (size_t)(_len - total));
      _len = (uint16_t)(_len - total);
    }
  }

  bool pop(Frame &out) {
    if (!_count) return false;
    out = _q[_head];
    _head = (uint8_t)((_head + 1) % QUEUE);
    _count--;
    return true;
  }
  uint8_t count() const { return _count; }

  uint32_t n_frames = 0, n_bad_crc = 0, n_bad_len = 0, n_dropped = 0, n_overflow = 0;

 private:
  void dropFirst() {
    memmove(_buf, _buf + 1, (size_t)(_len - 1));
    _len--;
  }
  int16_t findSync() const {
    for (uint16_t i = 0; i + 1 < _len; i++)
      if (_buf[i] == SYNC1 && _buf[i + 1] == SYNC2) return (int16_t)i;
    return -1;
  }

  Frame _q[QUEUE];
  uint8_t _head = 0, _tail = 0, _count = 0;
  uint8_t _buf[BUFSZ];
  uint16_t _len = 0;
};

// =========================================================================
//  BỘ MÃ HOÁ (ESP32 -> PC)  - khớp từng byte với cupfiller/protocol.py
// =========================================================================
inline size_t encHello(Writer &w, uint8_t seq, uint8_t version, uint8_t caps,
                       const uint16_t *presets, uint8_t n, uint16_t flow_x10) {
  w.begin(HELLO, seq);
  w.u8(version); w.u8(caps); w.u8(n); w.u8(0); w.u16(flow_x10);
  for (uint8_t i = 0; i < n && i < 8; i++) w.u16(presets[i]);
  return w.end();
}

inline size_t encCupPlaced(Writer &w, uint8_t seq, uint16_t dist_mm, uint16_t base_mm,
                           uint16_t height_mm, uint8_t flags, uint32_t uptime_ms) {
  w.begin(CUP_PLACED, seq);
  w.u16(dist_mm); w.u16(base_mm); w.u16(height_mm); w.u8(flags); w.u32(uptime_ms);
  return w.end();
}

inline size_t encCupRemoved(Writer &w, uint8_t seq, uint8_t reason, uint32_t uptime_ms) {
  w.begin(CUP_REMOVED, seq); w.u8(reason); w.u32(uptime_ms); return w.end();
}

inline size_t encPresetSelected(Writer &w, uint8_t seq, uint8_t index, uint16_t ml, uint8_t source) {
  w.begin(PRESET_SELECTED, seq); w.u8(index); w.u16(ml); w.u8(source); return w.end();
}

inline size_t encFillStarted(Writer &w, uint8_t seq, uint16_t ml, uint8_t mode, uint8_t source, uint16_t max_ml) {
  w.begin(FILL_STARTED, seq); w.u16(ml); w.u8(mode); w.u8(source); w.u16(max_ml); return w.end();
}

inline size_t encFillProgress(Writer &w, uint8_t seq, uint16_t poured, uint8_t pct, uint16_t target, uint8_t state) {
  w.begin(FILL_PROGRESS, seq); w.u16(poured); w.u8(pct); w.u16(target); w.u8(state); return w.end();
}

inline size_t encFillDone(Writer &w, uint8_t seq, uint16_t poured, uint16_t target, uint8_t status, uint32_t ms) {
  w.begin(FILL_DONE, seq); w.u16(poured); w.u16(target); w.u8(status); w.u32(ms); return w.end();
}

inline size_t encStatus(Writer &w, uint8_t seq, uint8_t state, uint8_t flags, uint16_t poured,
                        uint16_t target, uint16_t max_ml, uint32_t uptime_ms) {
  w.begin(STATUS, seq);
  w.u8(state); w.u8(flags); w.u16(poured); w.u16(target); w.u16(max_ml); w.u32(uptime_ms);
  return w.end();
}

inline size_t encVoiceEvent(Writer &w, uint8_t seq, uint8_t kind, uint8_t index,
                            uint8_t conf, uint8_t peaks, uint16_t rms, uint16_t dur_ms) {
  w.begin(VOICE_EVENT, seq);
  w.u8(kind); w.u8(index); w.u8(conf); w.u8(peaks); w.u16(rms); w.u16(dur_ms);
  return w.end();
}

inline size_t encAudioChunk(Writer &w, uint8_t seq, uint8_t seq8, uint8_t flags,
                            const int16_t *samples, uint16_t n) {
  w.begin(AUDIO_CHUNK, seq);
  w.u8(seq8); w.u8(flags); w.u16(n);
  for (uint16_t i = 0; i < n; i++) w.i16(samples[i]);
  return w.end();
}

inline size_t encButtonEvent(Writer &w, uint8_t seq, uint8_t index, uint8_t event,
                             uint16_t press_ms, uint8_t state) {
  w.begin(BUTTON_EVENT, seq);
  w.u8(index); w.u16(press_ms); w.u8(event); w.u8(state);
  return w.end();
}

inline size_t encError(Writer &w, uint8_t seq, uint8_t code, const char *text) {
  w.begin(ERROR, seq); w.u8(code); w.text(text, MAX_TEXT); return w.end();
}

inline size_t encAck(Writer &w, uint8_t seq, uint8_t acked, uint8_t status) {
  w.begin(ACK, seq); w.u8(acked); w.u8(status); return w.end();
}

inline size_t encPong(Writer &w, uint8_t seq, uint32_t uptime_ms, uint8_t state, uint8_t flags) {
  w.begin(PONG, seq); w.u32(uptime_ms); w.u8(state); w.u8(flags); return w.end();
}

inline size_t encLog(Writer &w, uint8_t seq, const char *text) {
  w.begin(LOG, seq); w.raw(text ? text : "", text ? strlen(text) : 0); return w.end();
}

// =========================================================================
//  BỘ GIẢI MÃ (PC -> ESP32)
// =========================================================================
struct HelloAck { uint8_t version, caps, n_presets; uint16_t presets[8]; };
inline bool parseHelloAck(const uint8_t *p, uint8_t n, HelloAck &o) {
  Reader r(p, n);
  o.version = r.u8(); o.caps = r.u8(); o.n_presets = r.u8();
  if (o.n_presets > 8) o.n_presets = 8;
  for (uint8_t i = 0; i < o.n_presets; i++) o.presets[i] = r.u16();
  return r.ok();
}

struct CupOk {
  uint16_t rim_r_x10, base_r_x10, height_x10, max_ml;
  uint8_t confidence, flags;
  char label[CUP_OK_LABEL_LEN];
  float rim_r_mm() const { return rim_r_x10 / 10.0f; }
  float base_r_mm() const { return base_r_x10 / 10.0f; }
  float height_mm() const { return height_x10 / 10.0f; }
};
inline bool parseCupOk(const uint8_t *p, uint8_t n, CupOk &o) {
  Reader r(p, n);
  o.rim_r_x10 = r.u16(); o.base_r_x10 = r.u16(); o.height_x10 = r.u16(); o.max_ml = r.u16();
  o.confidence = r.u8(); o.flags = r.u8();
  r.text(o.label, CUP_OK_LABEL_LEN);
  return r.ok();
}

struct CupReject { uint8_t reason; char text[MAX_TEXT]; };
inline bool parseCupReject(const uint8_t *p, uint8_t n, CupReject &o) {
  Reader r(p, n); o.reason = r.u8(); r.text(o.text, MAX_TEXT); return r.ok();
}

struct SetPreset { uint8_t index, source; uint16_t ml; };
inline bool parseSetPreset(const uint8_t *p, uint8_t n, SetPreset &o) {
  Reader r(p, n); o.index = r.u8(); o.ml = r.u16(); o.source = r.u8(); return r.ok();
}

struct StartFill { uint16_t ml, timeout_ms; uint8_t mode, source; };
inline bool parseStartFill(const uint8_t *p, uint8_t n, StartFill &o) {
  Reader r(p, n); o.ml = r.u16(); o.mode = r.u8(); o.source = r.u8(); o.timeout_ms = r.u16();
  return r.ok();
}

struct StopFill { uint8_t reason; };
inline bool parseStopFill(const uint8_t *p, uint8_t n, StopFill &o) {
  Reader r(p, n); o.reason = r.u8(); return r.ok();
}

struct SetMode { uint16_t on, off; };
inline bool parseSetMode(const uint8_t *p, uint8_t n, SetMode &o) {
  Reader r(p, n); o.on = r.u16(); o.off = r.u16(); return r.ok();
}

struct PumpSet { uint8_t duty_pct; uint16_t duration_ms; };
inline bool parsePumpSet(const uint8_t *p, uint8_t n, PumpSet &o) {
  Reader r(p, n); o.duty_pct = r.u8(); o.duration_ms = r.u16(); return r.ok();
}

struct SetParams { uint16_t flow_x10, max_ml, timeout_s, pulse_ms;   // 0xFFFF = giữ nguyên
  bool has_flow() const { return flow_x10 != 0xFFFF; }
  bool has_max()  const { return max_ml  != 0xFFFF; }
  bool has_to()   const { return timeout_s != 0xFFFF; }
  bool has_pulse()const { return pulse_ms != 0xFFFF; }
};
inline bool parseSetParams(const uint8_t *p, uint8_t n, SetParams &o) {
  Reader r(p, n);
  o.flow_x10 = r.u16(); o.max_ml = r.u16(); o.timeout_s = r.u16(); o.pulse_ms = r.u16();
  return r.ok();
}

struct CmdArg { uint8_t cmd; uint16_t arg; };
inline bool parseCmd(const uint8_t *p, uint8_t n, CmdArg &o) {
  Reader r(p, n); o.cmd = r.u8(); o.arg = r.u16(); return r.ok();
}

inline bool parsePing(const uint8_t *p, uint8_t n, uint32_t &uptime_ms) {
  Reader r(p, n); uptime_ms = r.u32(); return r.ok();
}

struct AckF { uint8_t acked, status; };
inline bool parseAck(const uint8_t *p, uint8_t n, AckF &o) {
  Reader r(p, n); o.acked = r.u8(); o.status = r.u8(); return r.ok();
}

struct ErrF { uint8_t code; char text[MAX_TEXT]; };
inline bool parseErrorPc(const uint8_t *p, uint8_t n, ErrF &o) {
  Reader r(p, n); o.code = r.u8(); r.text(o.text, MAX_TEXT); return r.ok();
}

// đọc thêm 1 mẫu 16-bit từ payload AUDIO_CHUNK (dùng cho test)
inline bool audioChunkInfo(const uint8_t *p, uint8_t n, uint8_t &seq8, uint8_t &flags, uint16_t &count) {
  Reader r(p, n); seq8 = r.u8(); flags = r.u8(); count = r.u16(); return r.ok();
}

const char *msgName(uint8_t m) {
  switch (m) {
    case HELLO: return "HELLO";         case CUP_PLACED: return "CUP_PLACED";
    case CUP_REMOVED: return "CUP_REMOVED"; case PRESET_SELECTED: return "PRESET_SELECTED";
    case FILL_STARTED: return "FILL_STARTED"; case FILL_PROGRESS: return "FILL_PROGRESS";
    case FILL_DONE: return "FILL_DONE";  case STATUS: return "STATUS";
    case VOICE_EVENT: return "VOICE_EVENT"; case AUDIO_CHUNK: return "AUDIO_CHUNK";
    case BUTTON_EVENT: return "BUTTON_EVENT"; case ERROR: return "ERROR";
    case ACK: return "ACK";             case PONG: return "PONG"; case LOG: return "LOG";
    case HELLO_ACK: return "HELLO_ACK"; case CUP_OK: return "CUP_OK";
    case CUP_REJECT: return "CUP_REJECT"; case SET_PRESET: return "SET_PRESET";
    case START_FILL: return "START_FILL"; case STOP_FILL: return "STOP_FILL";
    case SET_MODE: return "SET_MODE";   case PUMP_SET: return "PUMP_SET";
    case SET_PARAMS: return "SET_PARAMS"; case CMD: return "CMD"; case PING: return "PING";
    case ACK_PC: return "ACK_PC";       case ERROR_PC: return "ERROR_PC";
    default: return "?";
  }
}

}  // namespace proto
#endif  // CUPFILLER_PROTOCOL_H
