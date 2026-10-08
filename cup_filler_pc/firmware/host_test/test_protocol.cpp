// ===========================================================================
//  test_protocol.cpp - KIỂM TRA protocol.h (C++ của firmware) TRÊN MÁY TÍNH
//
//  Chạy bằng g++ (không cần ESP32):
//      g++ -std=c++11 -O2 -o /tmp/test_protocol firmware/host_test/test_protocol.cpp
//      /tmp/test_protocol firmware/host_test/protocol_vectors.txt
//
//  Nội dung kiểm tra:
//    1. CRC-16/CCITT-FALSE khớp giá trị chuẩn 0x29B1 của "123456789".
//    2. Với từng vector vàng trong protocol_vectors.txt:
//         - Decoder C++ giải mã frame bytes  -> msg/seq/payload phải khớp
//         - dir=esp2pc: bộ MÃ HOÁ C++ phải tạo ra ĐÚNG TỪNG BYTE như file vector
//         - dir=pc2esp: bộ GIẢI MÃ C++ phải đọc ra đúng các trường đã ghi
//    3. Chịu nhiễu: rác trước frame, frame CRC hỏng, frame cụt -> không làm mất
//       các frame hợp lệ đi sau.
//    4. Writer không ghi tràn bộ đệm khi payload quá dài.
//
//  So khớp từng byte với 'bản ghi trên dây' này là bằng chứng PC (Python) và
//  ESP32 (C++) nói cùng một thứ tiếng.
// ===========================================================================
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>

#include "protocol.h"

static int g_pass = 0;
static int g_fail = 0;

struct Vector {
  std::string name;
  uint8_t msg = 0;
  uint8_t seq = 0;
  std::string dir;
  std::vector<uint8_t> frame;     // bản ghi trên dây
  std::vector<uint8_t> payload;   // payload tách sẵn
  std::vector<std::pair<std::string, std::string> > fields;
};

static std::string trim(const std::string &s) {
  size_t a = s.find_first_not_of(" \t\r\n");
  if (a == std::string::npos) return "";
  size_t b = s.find_last_not_of(" \t\r\n");
  return s.substr(a, b - a + 1);
}

static std::vector<std::string> split(const std::string &s, char sep) {
  std::vector<std::string> out;
  std::string cur;
  for (size_t i = 0; i < s.size(); i++) {
    if (s[i] == sep) { out.push_back(cur); cur.clear(); }
    else cur += s[i];
  }
  out.push_back(cur);
  return out;
}

static std::vector<uint8_t> hexbytes(const std::string &s) {
  std::vector<uint8_t> out;
  std::vector<std::string> t = split(trim(s), ' ');
  for (size_t i = 0; i < t.size(); i++) {
    if (t[i].empty()) continue;
    out.push_back((uint8_t)strtol(t[i].c_str(), NULL, 16));
  }
  return out;
}

// Lấy giá trị các trường: fill fld_* theo vector hiện tại
static const Vector *g_cur = NULL;

static const char *fld(const char *key) {
  for (size_t i = 0; i < g_cur->fields.size(); i++)
    if (g_cur->fields[i].first == key) return g_cur->fields[i].second.c_str();
  return NULL;
}
static long fldi(const char *key) { const char *v = fld(key); return v ? strtol(v, NULL, 0) : -999999; }
static double fldf(const char *key) { const char *v = fld(key); return v ? atof(v) : -999999.0; }

static void ok(const char *what) { g_pass++; (void)what; }
static void bad(const char *what, const char *detail) {
  g_fail++;
  printf("  [SAI] %s: %s (%s)\n", g_cur ? g_cur->name.c_str() : "?", what, detail ? detail : "");
}

static void eqInt(const char *key, long got) {
  long want = fldi(key);
  if (want == -999999) { bad("thieu truong trong file vector", key); return; }
  if (want == got) ok(key);
  else {
    char buf[96];
    snprintf(buf, sizeof(buf), "doc duoc %ld, file vector ghi %ld", got, want);
    bad(key, buf);
  }
}

static void eqFlt(const char *key, double got, double tol) {
  double want = fldf(key);
  if (want == -999999.0) { bad("thieu truong trong file vector", key); return; }
  double d = want - got;
  if (d < 0) d = -d;
  if (d <= tol) ok(key);
  else {
    char buf[96];
    snprintf(buf, sizeof(buf), "doc duoc %.3f, file vector ghi %.3f", got, want);
    bad(key, buf);
  }
}

static void eqStr(const char *key, const char *got) {
  const char *want = fld(key);
  if (!want) { bad("thieu truong trong file vector", key); return; }
  if (strcmp(want, got) == 0) ok(key);
  else {
    std::string d = std::string("doc duoc '") + got + "', file vector ghi '" + want + "'";
    bad(key, d.c_str());
  }
}

static void eqFrame(const std::vector<uint8_t> &got) {
  const std::vector<uint8_t> &want = g_cur->frame;
  if (got.size() != want.size()) {
    char buf[96];
    snprintf(buf, sizeof(buf), "do dai %d byte, file vector %d byte",
             (int)got.size(), (int)want.size());
    bad("byte tren day", buf);
    return;
  }
  for (size_t i = 0; i < want.size(); i++) {
    if (got[i] != want[i]) {
      char buf[128];
      snprintf(buf, sizeof(buf), "byte %d = 0x%02X, file vector 0x%02X", (int)i, got[i], want[i]);
      bad("byte tren day", buf);
      return;
    }
  }
  ok("byte tren day");
}

// ---------------------------------------------------------------------------
static bool readVectors(const char *path, std::vector<Vector> &out) {
  FILE *fp = fopen(path, "r");
  if (!fp) { printf("KHONG MO DUOC FILE VECTOR: %s\n", path); return false; }
  char line[4096];
  while (fgets(line, sizeof(line), fp)) {
    std::string s = trim(line);
    if (s.empty() || s[0] == '#') continue;
    std::vector<std::string> parts = split(s, '|');
    if (parts.size() < 3) continue;
    Vector v;
    // ---- phần đầu: name msg=.. seq=.. dir=.. frame=..
    std::vector<std::string> head = split(trim(parts[0]), ' ');
    size_t idx = 0;
    v.name = head[idx++];
    for (; idx < head.size(); idx++) {
      const std::string &t = head[idx];
      if (t.compare(0, 4, "msg=") == 0) v.msg = (uint8_t)strtol(t.c_str() + 4, NULL, 16);
      else if (t.compare(0, 4, "seq=") == 0) v.seq = (uint8_t)strtol(t.c_str() + 4, NULL, 0);
      else if (t.compare(0, 4, "dir=") == 0) v.dir = t.substr(4);
      else if (t.compare(0, 6, "frame=") == 0) {
        std::string hex = t.substr(6);
        for (idx++; idx < head.size(); idx++) hex += " " + head[idx];  // các byte còn lại
        v.frame = hexbytes(hex);
        break;
      }
    }
    // ---- phần 2: payload=..
    std::string pay = trim(parts[1]);
    if (pay.compare(0, 8, "payload=") == 0) v.payload = hexbytes(pay.substr(8));
    // ---- phần 3: các trường key=value
    std::vector<std::string> ft = split(trim(parts[2]), ' ');
    for (size_t i = 0; i < ft.size(); i++) {
      size_t eq = ft[i].find('=');
      if (eq == std::string::npos) continue;
      v.fields.push_back(std::make_pair(ft[i].substr(0, eq), ft[i].substr(eq + 1)));
    }
    out.push_back(v);
  }
  fclose(fp);
  return true;
}

// ---------------------------------------------------------------------------
//  So khớp phần header/payload sau khi Decoder C++ giải mã frame
// ---------------------------------------------------------------------------
static void checkDecoded(const Vector &v) {
  proto::Decoder dec;
  std::vector<proto::Frame> got;
  // nhét thêm rác phía trước để thử khả năng tự đồng bộ lại
  const uint8_t junk[3] = {0x00, 0xAA, 0x99};
  for (int i = 0; i < 3; i++) dec.push(junk[i]);
  for (size_t i = 0; i < v.frame.size(); i++) {
    if (dec.push(v.frame[i])) {
      proto::Frame f;
      while (dec.pop(f)) got.push_back(f);
    }
  }
  proto::Frame f;
  while (dec.pop(f)) got.push_back(f);

  if (got.size() != 1) {
    char buf[64];
    snprintf(buf, sizeof(buf), "Decoder tra ve %d frame", (int)got.size());
    bad("so luong frame", buf);
    return;
  }
  if (got[0].msg != v.msg) { bad("msg", "khac ma goi tin trong file vector"); return; }
  if (got[0].seq != v.seq) { bad("seq", "khac so thu tu trong file vector"); return; }
  if (got[0].len != (uint8_t)v.payload.size() ||
      (got[0].len && memcmp(got[0].data, &v.payload[0], got[0].len) != 0)) {
    bad("payload", "khac payload trong file vector");
    return;
  }
  ok("decoder");
}

// ---------------------------------------------------------------------------
//  ESP32 -> PC : bộ mã hoá C++ phải ra ĐÚNG TỪNG BYTE như file vector
// ---------------------------------------------------------------------------
static std::vector<uint8_t> g_out;   // frame do C++ sinh ra (ghi ra file cho Python đọc lại)

static void checkEncodeEsp2Pc(const Vector &v) {
  uint8_t buf[512];
  proto::Writer w(buf, sizeof(buf));
  size_t n = 0;

  if (v.name.compare(0, 5, "hello") == 0) {
    uint16_t presets[8];
    uint8_t np = 0;
    const char *ps = fld("presets");
    if (ps) {
      std::vector<std::string> t = split(ps, ',');
      for (size_t i = 0; i < t.size() && i < 8; i++) presets[np++] = (uint16_t)atoi(t[i].c_str());
    }
    n = proto::encHello(w, v.seq, (uint8_t)fldi("version"), (uint8_t)fldi("caps"),
                        presets, np, (uint16_t)fldi("flow_ml_s_x10"));
  } else if (v.name.compare(0, 10, "cup_placed") == 0) {
    n = proto::encCupPlaced(w, v.seq, (uint16_t)fldi("distance_mm"), (uint16_t)fldi("baseline_mm"),
                            (uint16_t)fldi("height_mm"), (uint8_t)fldi("flags"),
                            (uint32_t)fldi("uptime_ms"));
  } else if (v.name.compare(0, 11, "cup_removed") == 0) {
    n = proto::encCupRemoved(w, v.seq, (uint8_t)fldi("reason"), (uint32_t)fldi("uptime_ms"));
  } else if (v.name.compare(0, 15, "preset_selected") == 0) {
    n = proto::encPresetSelected(w, v.seq, (uint8_t)fldi("index"), (uint16_t)fldi("ml"),
                                 (uint8_t)fldi("source"));
  } else if (v.name.compare(0, 12, "fill_started") == 0) {
    n = proto::encFillStarted(w, v.seq, (uint16_t)fldi("ml"), (uint8_t)fldi("mode"),
                              (uint8_t)fldi("source"), (uint16_t)fldi("max_ml"));
  } else if (v.name.compare(0, 13, "fill_progress") == 0) {
    n = proto::encFillProgress(w, v.seq, (uint16_t)fldi("poured_ml"), (uint8_t)fldi("pct"),
                               (uint16_t)fldi("target_ml"), (uint8_t)fldi("state"));
  } else if (v.name.compare(0, 9, "fill_done") == 0) {
    n = proto::encFillDone(w, v.seq, (uint16_t)fldi("poured_ml"), (uint16_t)fldi("target_ml"),
                           (uint8_t)fldi("status"), (uint32_t)fldi("elapsed_ms"));
  } else if (v.name.compare(0, 6, "status") == 0) {
    n = proto::encStatus(w, v.seq, (uint8_t)fldi("state"), (uint8_t)fldi("flags"),
                         (uint16_t)fldi("poured_ml"), (uint16_t)fldi("target_ml"),
                         (uint16_t)fldi("max_ml"), (uint32_t)fldi("uptime_ms"));
  } else if (v.name.compare(0, 11, "voice_event") == 0) {
    n = proto::encVoiceEvent(w, v.seq, (uint8_t)fldi("kind"), (uint8_t)fldi("index"),
                             (uint8_t)fldi("confidence"), (uint8_t)fldi("n_peaks"),
                             (uint16_t)fldi("rms"), (uint16_t)fldi("dur_ms"));
  } else if (v.name.compare(0, 11, "audio_chunk") == 0) {
    // lấy mẫu từ chính payload của file vector (4 byte đầu là seq8/flags/n)
    std::vector<int16_t> samples;
    for (size_t i = 4; i + 1 < v.payload.size(); i += 2) {
      int16_t s = (int16_t)((uint16_t)v.payload[i] | ((uint16_t)v.payload[i + 1] << 8));
      samples.push_back(s);
    }
    uint16_t rate = (fld("rate_hz") != NULL) ? (uint16_t)fldi("rate_hz") : 0;
    n = proto::encAudioChunk(w, v.seq, (uint8_t)fldi("seq8"), (uint8_t)fldi("flags"),
                             samples.empty() ? NULL : &samples[0], (uint16_t)samples.size(),
                             rate);
    if (proto::audioRateHz((uint8_t)fldi("flags")) != rate) {
      char b[64];
      snprintf(b, sizeof(b), "audioRateHz tra ve %u, file vector ghi %u",
               (unsigned)proto::audioRateHz((uint8_t)fldi("flags")), (unsigned)rate);
      bad("ma tan so lay mau", b);
    }
  } else if (v.name.compare(0, 6, "button") == 0) {
    n = proto::encButtonEvent(w, v.seq, (uint8_t)fldi("index"), (uint8_t)fldi("event"),
                              (uint16_t)fldi("press_ms"), (uint8_t)fldi("state"));
  } else if (v.name.compare(0, 9, "error_esp") == 0) {
    n = proto::encError(w, v.seq, (uint8_t)fldi("code"), fld("text"));
  } else if (v.name.compare(0, 7, "ack_esp") == 0) {
    n = proto::encAck(w, v.seq, (uint8_t)fldi("acked_msg"), (uint8_t)fldi("status"));
  } else if (v.name.compare(0, 4, "pong") == 0) {
    n = proto::encPong(w, v.seq, (uint32_t)fldi("uptime_ms"), (uint8_t)fldi("state"),
                       (uint8_t)fldi("flags"));
  } else if (v.name.compare(0, 3, "log") == 0) {
    n = proto::encLog(w, v.seq, fld("text"));
  } else {
    bad("vector la", "khong biet cach ma hoa");
    return;
  }

  if (n == 0) { bad("ma hoa", "Writer tu choi (thieu cho?)"); return; }
  std::vector<uint8_t> got(buf, buf + n);
  eqFrame(got);
  g_out.insert(g_out.end(), got.begin(), got.end());   // cho Python đọc lại
}

// ---------------------------------------------------------------------------
//  PC -> ESP32 : bộ giải mã C++ phải đọc ra đúng các trường
// ---------------------------------------------------------------------------
static void checkDecodePc2Esp(const Vector &v) {
  const uint8_t *p = v.payload.empty() ? NULL : &v.payload[0];
  uint8_t n = (uint8_t)v.payload.size();

  if (v.name == "hello_ack") {
    proto::HelloAck ha;
    if (!proto::parseHelloAck(p, n, ha)) { bad("parseHelloAck", "tra ve false"); return; }
    eqInt("version", ha.version);
    eqInt("caps", ha.caps);
    std::string presets;
    for (uint8_t i = 0; i < ha.n_presets; i++) {
      char b[16];
      snprintf(b, sizeof(b), i ? ",%u" : "%u", (unsigned)ha.presets[i]);
      presets += b;
    }
    eqStr("presets", presets.c_str());
  } else if (v.name.compare(0, 6, "cup_ok") == 0) {
    proto::CupOk c;
    if (!proto::parseCupOk(p, n, c)) { bad("parseCupOk", "tra ve false"); return; }
    eqFlt("rim_r_mm", c.rim_r_mm(), 0.05);
    eqFlt("base_r_mm", c.base_r_mm(), 0.05);
    eqFlt("height_mm", c.height_mm(), 0.05);
    eqInt("max_ml", c.max_ml);
    eqFlt("confidence", c.confidence / 100.0, 0.005);
    eqInt("flags", c.flags);
    eqStr("label", c.label);
  } else if (v.name.compare(0, 10, "cup_reject") == 0) {
    proto::CupReject r;
    if (!proto::parseCupReject(p, n, r)) { bad("parseCupReject", "tra ve false"); return; }
    eqInt("reason", r.reason);
    eqStr("text", r.text);
  } else if (v.name.compare(0, 10, "set_preset") == 0) {
    proto::SetPreset s;
    if (!proto::parseSetPreset(p, n, s)) { bad("parseSetPreset", "tra ve false"); return; }
    eqInt("index", s.index);
    eqInt("ml", s.ml);
    eqInt("source", s.source);
  } else if (v.name.compare(0, 10, "start_fill") == 0) {
    proto::StartFill s;
    if (!proto::parseStartFill(p, n, s)) { bad("parseStartFill", "tra ve false"); return; }
    eqInt("ml", s.ml);
    eqInt("mode", s.mode);
    eqInt("source", s.source);
    eqInt("timeout_ms", s.timeout_ms);
  } else if (v.name.compare(0, 9, "stop_fill") == 0) {
    proto::StopFill s;
    if (!proto::parseStopFill(p, n, s)) { bad("parseStopFill", "tra ve false"); return; }
    eqInt("reason", s.reason);
  } else if (v.name.compare(0, 8, "set_mode") == 0) {
    proto::SetMode s;
    if (!proto::parseSetMode(p, n, s)) { bad("parseSetMode", "tra ve false"); return; }
    eqInt("on", s.on);
    eqInt("off", s.off);
  } else if (v.name.compare(0, 8, "pump_set") == 0) {
    proto::PumpSet s;
    if (!proto::parsePumpSet(p, n, s)) { bad("parsePumpSet", "tra ve false"); return; }
    eqInt("duty_pct", s.duty_pct);
    eqInt("duration_ms", s.duration_ms);
  } else if (v.name.compare(0, 10, "set_params") == 0) {
    proto::SetParams s;
    if (!proto::parseSetParams(p, n, s)) { bad("parseSetParams", "tra ve false"); return; }
    eqInt("flow_ml_s_x10", s.flow_x10);
    eqInt("max_ml", s.max_ml);
    eqInt("timeout_s", s.timeout_s);
    eqInt("pulse_ms", s.pulse_ms);
  } else if (v.name.compare(0, 3, "cmd") == 0) {
    proto::CmdArg c;
    if (!proto::parseCmd(p, n, c)) { bad("parseCmd", "tra ve false"); return; }
    eqInt("cmd", c.cmd);
    eqInt("arg", c.arg);
  } else if (v.name.compare(0, 4, "ping") == 0) {
    uint32_t up = 0;
    if (!proto::parsePing(p, n, up)) { bad("parsePing", "tra ve false"); return; }
    eqInt("uptime_ms", (long)up);
  } else {
    bad("vector la", "khong biet cach giai ma");
  }
}

// ---------------------------------------------------------------------------
//  Kiểm tra chịu nhiễu + an toàn bộ đệm
// ---------------------------------------------------------------------------
static void testNoiseAndOverflow(const std::vector<Vector> &all) {
  // 1) Ghép tất cả frame esp2pc + rác xen kẽ + 1 frame bị hỏng CRC
  proto::Decoder dec;
  std::vector<proto::Frame> got;
  size_t expect = 0;
  const uint8_t junk[5] = {0x13, 0x37, 0xAA, 0x99, 0x55};
  for (size_t i = 0; i < all.size(); i++) {
    const Vector &v = all[i];
    if (v.dir != "esp2pc") continue;
    for (int k = 0; k < 5; k++) dec.push(junk[k]);
    for (size_t b = 0; b < v.frame.size(); b++) {
      if (dec.push(v.frame[b])) {
        proto::Frame f;
        while (dec.pop(f)) got.push_back(f);
      }
    }
    expect++;
  }
  // frame hỏng: lấy frame đầu tiên, lật 1 bit trong payload rồi nối 1 frame đúng
  {
    const Vector *first = NULL, *second = NULL;
    for (size_t i = 0; i < all.size(); i++) {
      if (all[i].dir != "esp2pc") continue;
      if (!first) first = &all[i];
      else if (!second) second = &all[i];
    }
    if (first && second) {
      std::vector<uint8_t> badFrame = first->frame;
      badFrame[6] ^= 0x01;                       // hỏng payload -> CRC sai
      for (size_t b = 0; b < badFrame.size(); b++) dec.push(badFrame[b]);
      for (size_t b = 0; b < second->frame.size(); b++) dec.push(second->frame[b]);
      expect++;
    }
  }
  {
    proto::Frame f;
    while (dec.pop(f)) got.push_back(f);
  }
  if (got.size() == expect) {
    printf("  [OK ] chiu nhieu: %d/%d frame (rac + CRC hong + frame cut)\n",
           (int)got.size(), (int)expect);
    g_pass++;
  } else {
    printf("  [SAI] chiu nhieu: chi nhan %d/%d frame\n", (int)got.size(), (int)expect);
    g_fail++;
  }

  // 2) Writer phải từ chối khi bộ đệm quá nhỏ (không ghi tràn)
  uint8_t small[16];
  proto::Writer w(small, sizeof(small));
  int16_t samples[96];
  for (int i = 0; i < 96; i++) samples[i] = (int16_t)i;
  size_t n = proto::encAudioChunk(w, 1, 0, 0, samples, 96);
  if (n == 0) {
    printf("  [OK ] Writer tu choi ghi tran (payload 196 byte vao dem 16 byte)\n");
    g_pass++;
  } else {
    printf("  [SAI] Writer ghi tran! n=%d\n", (int)n);
    g_fail++;
  }

  // 3) Frame cụt: chỉ nửa đầu của 1 frame -> không được tạo frame rác
  {
    proto::Decoder d2;
    const Vector *v = NULL;
    for (size_t i = 0; i < all.size(); i++)
      if (all[i].dir == "esp2pc" && all[i].frame.size() > 10) { v = &all[i]; break; }
    bool any = false;
    if (v) {
      for (size_t b = 0; b < v->frame.size() / 2; b++)
        if (d2.push(v->frame[b])) any = true;
      proto::Frame f;
      while (d2.pop(f)) any = true;
    }
    if (!any) { printf("  [OK ] frame cut khong sinh frame rac\n"); g_pass++; }
    else { printf("  [SAI] frame cut sinh frame rac\n"); g_fail++; }
  }
}

// ---------------------------------------------------------------------------
int main(int argc, char **argv) {
  const char *path = (argc > 1) ? argv[1] : "firmware/host_test/protocol_vectors.txt";

  // (1) CRC chuẩn
  const char *crcCheck = "123456789";
  uint16_t crc = proto::crc16_ccitt((const uint8_t *)crcCheck, 9);
  if (crc == 0x29B1) {
    printf("  [OK ] CRC-16/CCITT-FALSE(\"123456789\") = 0x29B1\n");
    g_pass++;
  } else {
    printf("  [SAI] CRC = 0x%04X (mong doi 0x29B1)\n", crc);
    g_fail++;
  }

  std::vector<Vector> all;
  if (!readVectors(path, all)) return 2;
  printf("Doc %d vector tu %s\n", (int)all.size(), path);

  size_t n_esp2pc = 0, n_pc2esp = 0;
  for (size_t i = 0; i < all.size(); i++) {
    const Vector &v = all[i];
    g_cur = &v;
    checkDecoded(v);
    if (v.dir == "esp2pc") { checkEncodeEsp2Pc(v); n_esp2pc++; }
    else                   { checkDecodePc2Esp(v); n_pc2esp++; }
  }

  printf("  ESP32 -> PC : %d vector (ma hoa C++ == file vector)\n", (int)n_esp2pc);
  printf("  PC -> ESP32 : %d vector (giai ma C++ == truong trong file vector)\n", (int)n_pc2esp);

  printf("Kiem tra chiu nhieu & an toan bo dem:\n");
  testNoiseAndOverflow(all);

  if (argc > 2) {                     // ghi các frame ESP->PC đã mã hoá cho Python đọc lại
    FILE *fp = fopen(argv[2], "wb");
    if (fp) {
      if (!g_out.empty()) fwrite(&g_out[0], 1, g_out.size(), fp);
      fclose(fp);
      printf("Da ghi %d byte frame do C++ sinh ra vao %s\n", (int)g_out.size(), argv[2]);
    } else {
      printf("[SAI] khong ghi duoc %s\n", argv[2]);
      g_fail++;
    }
  }

  printf("\nKET QUA: %d dung / %d sai\n", g_pass, g_fail);
  if (g_fail == 0) {
    printf("khop protocol.py tung byte\n");
    return 0;
  }
  return 1;
}
