// =====================================================================
//  UNIT TEST cho firmware ESP32 - chay tren PC bang g++ (make test)
//  Khong can Arduino, khong can nap ESP32: dung host/board_io_host.cpp
//  lam "phan cung ao" (GPIO gia lap + dong ho ao).
//
//  Kiem tra:
//   [A] giao thuc UART (ma hoa / giai ma / checksum / chong nhiem)
//   [B] cam bien coc -> bao CUP,1 -> doi may tinh xac nhan ACK,1
//   [C] chi sau khi ARMED moi cho phep nut bam / mic bat bom
//   [D] soft-PWM relay theo lenh PUMP cua may tinh
//   [E] an toan: nha coc / mat may tinh / chua xac nhan -> cat relay
// =====================================================================
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "config.h"
#include "device_fsm.h"
#include "host/host_board.h"
#include "uart_protocol.h"

using namespace cupfiller;

static int g_pass = 0;
static int g_fail = 0;

#define CHECK(cond, what)                                                   \
  do {                                                                     \
    if (cond) {                                                            \
      ++g_pass;                                                            \
    } else {                                                               \
      ++g_fail;                                                            \
      printf("  [FAIL] %s  (dong %d)\n", (what), __LINE__);                 \
    }                                                                      \
  } while (0)

#define CHECK_EQ_INT(a, b, what)                                            \
  do {                                                                     \
    long _a = (long)(a), _b = (long)(b);                                    \
    if (_a == _b) {                                                        \
      ++g_pass;                                                            \
    } else {                                                               \
      ++g_fail;                                                            \
      printf("  [FAIL] %s: duoc %ld, mong %ld  (dong %d)\n", (what), _a, _b, \
             __LINE__);                                                     \
    }                                                                      \
  } while (0)

// ---- bo giai ma doc lap (khong dung FrameParser de tranh kiem tra vong) ----
static std::vector<std::string> parse_frames(const std::string &s) {
  std::vector<std::string> out;
  size_t i = 0;
  while (i < s.size()) {
    if (s[i] != '$') { ++i; continue; }
    const size_t star = s.find('*', i);
    if (star == std::string::npos) break;
    const std::string body = s.substr(i + 1, star - i - 1);
    if (star + 2 >= s.size()) break;
    char hex[3] = {s[star + 1], s[star + 2], 0};
    char buf[96];
    snprintf(buf, sizeof(buf), "%02x", frame_checksum(body.c_str(), body.size()));
    if (strcmp(buf, hex) == 0) out.push_back(body);
    i = star + 3;
  }
  return out;
}

static bool has_msg(const std::vector<std::string> &v, const char *want) {
  for (size_t i = 0; i < v.size(); ++i)
    if (v[i] == want) return true;
  return false;
}

static bool has_type(const std::vector<std::string> &v, const char *type) {
  const size_t n = strlen(type);
  for (size_t i = 0; i < v.size(); ++i)
    if (v[i].compare(0, n, type) == 0 && (v[i].size() == n || v[i][n] == ',')) return true;
  return false;
}

// ---------------------------------------------------------------------------
// "ESP32 ao": FSM + GPIO gia lap + dong ho ao
struct Board {
  DeviceFSM fsm;
  uint32_t t;

  Board() : t(0) {
    host::reset_board();
    host::set_millis(0);
    fsm.begin();
    host::link_drain();  // bo qua $BOOT
  }

  // chay FSM them `ms` mili giay, buoc `step` ms
  void run(uint32_t ms, uint32_t step = 5) {
    for (uint32_t k = 0; k < ms; k += step) {
      t += step;
      host::set_millis(t);
      fsm.loop();
    }
  }

  // chay va GIUA NHIP voi may tinh (may tinh that gui $HB moi 250 ms)
  void run_live(uint32_t ms, uint32_t step = 5) {
    for (uint32_t k = 0; k < ms; k += step) {
      if (k % 200 == 0) pc_send("HB", "1");
      run(step);
    }
  }

  std::vector<std::string> read() { return parse_frames(host::link_drain()); }

  void pc_send(const char *type, const char *args = "") {
    char buf[96];
    encode_frame(buf, sizeof(buf), type, args);
    host::link_inject(std::string(buf));
  }

  // dat muc chan dau vao (0 = kich hoat vi nut/cam bien tich cuc THAP)
  void pin(int p, int lvl) { host::set_pin(p, lvl); }

  void place_cup() { pin(PIN_CUP_SENSOR, CUP_ACTIVE_LEVEL); }
  void remove_cup() { pin(PIN_CUP_SENSOR, CUP_ACTIVE_LEVEL ? 0 : 1); }
  void press(int pin_no) { pin(pin_no, BTN_ACTIVE_LEVEL); }
  void release(int pin_no) { pin(pin_no, BTN_ACTIVE_LEVEL ? 0 : 1); }

  // dua FSM ve trang thai ARMED (da co coc + may tinh da xac nhan)
  void arm() {
    pc_send("HELLO", "1");
    run(10);
    place_cup();
    run(300);
    pc_send("ACK", "1");
    run(10);
  }

  // nhan nut muc `idx` kieu "cham roi tha" (nhanh hon nguong giu lau)
  void tap_level(int idx) {
    press(kLevelPins[idx]);
    run(60);
    release(kLevelPins[idx]);
    run(60);
  }
};

// ---------------------------------------------------------------------------
static void test_protocol() {
  printf("[A] giao thuc UART\n");

  char buf[96];
  const size_t n = encode_frame(buf, sizeof(buf), "CUP", "1");
  CHECK(strcmp(buf, "$CUP,1*45\n") == 0, "ma hoa $CUP,1*45");
  CHECK_EQ_INT(n, 10, "do dai khuung $CUP,1*45");
  CHECK_EQ_INT(frame_checksum("CUP,1", 5), 0x45, "checksum CUP,1");

  encode_frame(buf, sizeof(buf), "ACK", "");
  CHECK(strcmp(buf, "$ACK*cf\n") == 0, "ma hoa khong tham so $ACK*cf");

  // giai ma: rac + khuung hop le
  FrameParser p;
  bool got = false;
  const char *junk = "\x00\xff ROM boot garbage\r\n";
  for (const char *q = junk; *q; ++q) got |= p.feed((uint8_t)*q);
  CHECK(!got, "bo qua rac truoc khuung tin");
  for (const char *q = "$CUP,1*45\n"; *q; ++q) got |= p.feed((uint8_t)*q);
  CHECK(got, "nhan duoc khuung CUP,1");
  CHECK(strcmp(p.type(), "CUP") == 0, "tach dung TYPE");
  CHECK(strcmp(p.args(), "1") == 0, "tach dung ARGS");

  // sai checksum -> huy, dem loi
  FrameParser p2;
  got = false;
  for (const char *q = "$CUP,1*99\n"; *q; ++q) got |= p2.feed((uint8_t)*q);
  CHECK(!got, "tu choi khuung sai checksum");
  CHECK_EQ_INT(p2.errors(), 1, "dem 1 loi checksum");

  // dong bo lai khi gap '$' giua chung
  FrameParser p3;
  got = false;
  {
    char good[96];
    encode_frame(good, sizeof(good), "PUMP", "60");
    const std::string broken = std::string("$CU") + good;  // khuung dut roi
    for (size_t i = 0; i < broken.size(); ++i) got |= p3.feed((uint8_t)broken[i]);
  }
  CHECK(got && strcmp(p3.type(), "PUMP") == 0, "dong bo lai sau '$' giua khuung");

  // khong can ky tu xuong dong cuoi van nhan
  FrameParser p4;
  got = false;
  {
    char hb[96];
    encode_frame(hb, sizeof(hb), "HB", "12,IDLE,1,0,0");
    hb[strlen(hb) - 1] = '\0';  // cat ky tu xuong dong
    for (const char *q = hb; *q; ++q) got |= p4.feed((uint8_t)*q);
  }
  CHECK(got, "chap nhan khuung thieu ky tu xuong dong cuoi");

  // khuung qua dai -> huy, khong tran bo dem
  FrameParser p5;
  got = false;
  p5.feed('$');
  for (int i = 0; i < 200; ++i) got |= p5.feed('A');
  CHECK(!got, "khung qua dai bi huy");
  CHECK(p5.errors() >= 1, "dem loi khuung qua dai");

  // tach tham so
  char out[16];
  CHECK(get_arg("3,200,ON", 1, out, sizeof(out)) && strcmp(out, "200") == 0, "get_arg index 1");
  CHECK(get_arg("3,200,ON", 2, out, sizeof(out)) && strcmp(out, "ON") == 0, "get_arg index 2");
  CHECK(!get_arg("3,200", 5, out, sizeof(out)), "get_arg vuot so tham so -> false");
  CHECK_EQ_INT(get_arg_int("PUMP,-5", 1, 7), -5, "get_arg_int so am");
  CHECK_EQ_INT(get_arg_int("abc", 0, 7), 7, "get_arg_int chu -> gia tri mac dinh");
  CHECK_EQ_INT(get_arg_int("100", 0, 0), 100, "get_arg_int binh thuong");
}

// ---------------------------------------------------------------------------
static void test_boot_and_sensor() {
  printf("[B] cam bien coc -> bao may tinh -> doi xac nhan\n");

  Board b;
  CHECK(b.fsm.state() == DevState::IDLE, "khoi dong xong o trang thai IDLE");
  CHECK(!b.fsm.relay_on(), "relay TAT ngay khi khoi dong (tich cuc CAO = 0)");
  CHECK_EQ_INT(host::get_pin(PIN_RELAY_PUMP), 0, "chan relay o muc THAP luc khoi dong");

  // chua ket noi may tinh -> KHONG bao CUP (may tinh chua nghe duoc)
  b.place_cup();
  b.run(400);
  std::vector<std::string> m = b.read();
  CHECK(!has_type(m, "CUP"), "chua co may tinh -> chua bao CUP");
  CHECK(!b.fsm.pc_online(), "pc_online = false khi chua co HELLO");

  // may tinh bat dau noi chuyen -> bao CUP,1 ngay
  b.pc_send("HELLO", "1");
  b.run(10);
  m = b.read();
  CHECK(has_msg(m, "CUP,1"), "co HELLO -> gui CUP,1");
  CHECK(b.fsm.state() == DevState::WAIT_ACK, "chuyen sang WAIT_ACK");
  CHECK(!b.fsm.armed(), "chua ARMED khi may tinh chua xac nhan");

  // het thoi gian cho -> bao loi va tro ve IDLE (khong bao gio tu ARM)
  b.run(ACK_TIMEOUT_MS + 200);
  m = b.read();
  CHECK(has_type(m, "ERR"), "qua han doi ACK -> bao ERR");
  CHECK(b.fsm.state() == DevState::IDLE, "qua han doi ACK -> ve IDLE");

  // may tinh bao "khong thay coc trong anh" -> khong duoc bom
  b.pc_send("HELLO", "1");       // may tinh van con song
  b.run_live(3000);              // doi den lan bao lai CUP,1
  m = b.read();
  CHECK(has_msg(m, "CUP,1"), "thu bao lai CUP,1 sau khi loi");
  b.pc_send("ACK", "0");
  b.run(20);
  m = b.read();
  CHECK(has_msg(m, "ERR,NO_CUP_VISION"), "nhan ACK,0 -> bao NO_CUP_VISION");
  CHECK(b.fsm.state() == DevState::IDLE, "nhan ACK,0 -> ve IDLE");

  // dat coc lai + xac nhan thanh cong -> ARMED
  b.remove_cup();
  b.run(300);
  b.place_cup();
  b.run(300);
  m = b.read();
  CHECK(has_msg(m, "CUP,1"), "dat coc lai -> bao CUP,1");
  b.pc_send("ACK", "1");
  b.run(20);
  CHECK(b.fsm.state() == DevState::ARMED, "nhan ACK,1 -> ARMED (cho phep nguoi dung)");
  CHECK(b.fsm.armed(), "armed() = true");
}

// ---------------------------------------------------------------------------
static void test_ack_backoff() {
  printf("[B2] may tinh khong thay coc: bao lai co gioi han, doi nhac coc\n");
  Board b;
  b.pc_send("HELLO", "1");
  b.run(10);
  b.place_cup();
  b.run_live(300);
  b.read();

  // 3 lan may tinh bao "khong thay coc" -> ESP phai ngung bao lien tuc
  for (int i = 0; i < 3; ++i) {
    b.pc_send("ACK", "0");
    b.run_live(CUP_RETRY_MS + 400);
  }
  b.read();                      // bo qua cac lan bao truoc
  b.run_live(6000);              // cho lau: khong duoc bao CUP nua
  std::vector<std::string> m = b.read();
  CHECK(!has_type(m, "CUP"), "that bai 3 lan -> ngung bao CUP (cho nhac coc)");
  CHECK(b.fsm.state() == DevState::IDLE, "van o IDLE, khong tu ARM");

  // nhac coc ra roi dat lai -> cho phep thu lai tu dau
  b.remove_cup();
  b.run_live(400);
  b.read();
  b.place_cup();
  b.run_live(400);
  m = b.read();
  CHECK(has_msg(m, "CUP,1"), "dat coc lai -> bao CUP,1 de may tinh xem lai");
  b.pc_send("ACK", "1");
  b.run_live(20);
  CHECK(b.fsm.state() == DevState::ARMED, "lan nay duoc xac nhan -> ARMED");
}

// ---------------------------------------------------------------------------
static void test_button_and_pump() {
  printf("[C+D] nut bam -> START -> bom theo PUMP\n");

  Board b;
  b.arm();
  CHECK(b.fsm.state() == DevState::ARMED, "arm() dua ve ARMED");

  // nhan nut muc 3 (200 ml)
  b.tap_level(2);
  std::vector<std::string> m = b.read();
  CHECK(has_msg(m, "BTN,3,DOWN"), "gui BTN,3,DOWN");
  CHECK(has_msg(m, "LVL,200"), "gui LVL,200");
  CHECK(has_msg(m, "START,200"), "gui START,200");
  CHECK(b.fsm.state() == DevState::FILLING, "chuyen sang FILLING");

  // may tinh ra lenh bom 60% -> relay bam mem
  b.pc_send("PUMP", "60");
  b.run(20);
  CHECK_EQ_INT(b.fsm.duty(), 60, "ghi nhan duty 60");
  host::clear_writes(PIN_RELAY_PUMP);
  bool saw_on = false, saw_off = false;
  for (int i = 0; i < 20; ++i) {  // 1.2 s = 3 chu ky soft-PWM
    b.run_live(60);
    if (b.fsm.relay_on()) saw_on = true;
    else saw_off = true;
  }
  CHECK(saw_on, "relay co luc ON (soft-PWM)");
  CHECK(saw_off, "relay co luc OFF (soft-PWM)");
  CHECK(host::writes_on(PIN_RELAY_PUMP) >= 4, "relay doi muc it nhat 4 lan trong 1.2 s");
  CHECK_EQ_INT(host::get_pin(PIN_RELAY_PUMP), b.fsm.relay_on() ? 1 : 0,
               "muc chan relay dung cuc tinh (tich cuc CAO)");

  // lenh PUMP bi kep gioi han 0..100
  b.pc_send("PUMP", "500");
  b.run_live(10);
  CHECK_EQ_INT(b.fsm.duty(), 100, "PUMP,500 -> kep ve 100");
  b.pc_send("PUMP", "-3");
  b.run_live(10);
  CHECK_EQ_INT(b.fsm.duty(), 0, "PUMP,-3 -> kep ve 0");

  // may tinh bao xong -> ARMED, relay tat
  b.pc_send("PUMP", "40");
  b.run_live(10);
  b.pc_send("STATE", "DONE");
  b.run_live(20);
  m = b.read();
  CHECK(b.fsm.state() == DevState::ARMED, "nhan STATE,DONE -> ve ARMED");
  CHECK_EQ_INT(b.fsm.duty(), 0, "xong rot -> duty 0");
  CHECK(!b.fsm.relay_on(), "xong rot -> relay OFF");
  CHECK(has_type(m, "DONE"), "gui DONE len may tinh");
}

// ---------------------------------------------------------------------------
static void test_safety() {
  printf("[E] an toan: chua ARMED / nha coc / mat may tinh\n");

  // (1) chua duoc xac nhan -> nhan nut KHONG duoc bom
  {
    Board b;
    b.pc_send("HELLO", "1");
    b.run(10);
    b.place_cup();
    b.run(300);  // WAIT_ACK
    b.read();
    b.tap_level(1);
    std::vector<std::string> m = b.read();
    CHECK(has_msg(m, "ERR,NOT_ACKED"), "chua ACK -> bao NOT_ACKED");
    CHECK(!has_type(m, "START"), "chua ACK -> khong gui START");
    CHECK_EQ_INT(b.fsm.duty(), 0, "chua ACK -> duty = 0");
    b.pc_send("PUMP", "80");  // may tinh co tinh gui lenh bom
    b.run_live(500);
    CHECK(!b.fsm.relay_on(), "khong ARMED -> relay van OFF du co lenh PUMP");
  }

  // (2) nha coc giua chung -> cat relay NGAY, bao STOP,CUP_REMOVED
  {
    Board b;
    b.arm();
    b.tap_level(2);
    b.pc_send("PUMP", "70");
    b.run_live(300);
    CHECK(b.fsm.state() == DevState::FILLING, "dang FILLING truoc khi nha coc");
    b.remove_cup();
    b.run(200);
    std::vector<std::string> m = b.read();
    CHECK(!b.fsm.relay_on(), "nha coc -> relay OFF");
    CHECK_EQ_INT(host::get_pin(PIN_RELAY_PUMP), 0, "nha coc -> chan relay muc THAP");
    CHECK(has_msg(m, "STOP,CUP_REMOVED"), "nha coc -> bao STOP,CUP_REMOVED");
    CHECK(has_msg(m, "CUP,0"), "nha coc -> bao CUP,0");
    CHECK(b.fsm.state() == DevState::IDLE, "nha coc -> ve IDLE");
  }

  // (3) mat lien lac voi may tinh -> cat bom, ve IDLE
  {
    Board b;
    b.arm();
    b.tap_level(2);
    b.pc_send("PUMP", "70");
    b.run(200);
    b.read();
    b.run(PC_TIMEOUT_MS + 200);  // im lang, khong gui $HB nua
    std::vector<std::string> m = b.read();
    CHECK(!b.fsm.relay_on(), "mat may tinh -> relay OFF");
    CHECK(has_msg(m, "STOP,PC_TIMEOUT"), "mat may tinh -> bao STOP,PC_TIMEOUT");
    CHECK(b.fsm.state() == DevState::IDLE, "mat may tinh -> ve IDLE");
  }

  // (4) nut DUNG khi dang rot
  {
    Board b;
    b.arm();
    b.tap_level(4);
    b.pc_send("PUMP", "50");
    b.run_live(200);
    b.press(PIN_BTN_STOP);
    b.run_live(60);
    b.release(PIN_BTN_STOP);
    b.run_live(60);
    std::vector<std::string> m = b.read();
    CHECK(has_msg(m, "STOP,BTN_STOP"), "nut DUNG -> bao STOP,BTN_STOP");
    CHECK(!b.fsm.relay_on(), "nut DUNG -> relay OFF");
    CHECK(b.fsm.state() == DevState::ARMED, "nut DUNG -> ve ARMED (van con coc)");
  }
}

// ---------------------------------------------------------------------------
static void test_voice_and_config() {
  printf("[F] mic (giong noi) va cap nhat muc rot tu may tinh\n");

  // mic: noi "muc 5" -> bat dau rot 300 ml
  {
    Board b;
    b.arm();
    b.fsm.inject_voice(VOICE_LEVEL_5);
    b.run(20);
    std::vector<std::string> m = b.read();
    CHECK(has_msg(m, "LVL,300"), "mic -> gui LVL,300");
    CHECK(has_msg(m, "START,300"), "mic -> gui START,300");
    CHECK(b.fsm.state() == DevState::FILLING, "mic -> FILLING");
    b.pc_send("PUMP", "45");
    b.run_live(50);
    CHECK_EQ_INT(b.fsm.duty(), 45, "mic: bom chay theo PUMP");
  }

  // mic: "dung lai" khi dang rot
  {
    Board b;
    b.arm();
    b.fsm.inject_voice(VOICE_LEVEL_3);
    b.run(20);
    b.pc_send("PUMP", "45");
    b.run_live(50);
    b.fsm.inject_voice(VOICE_STOP);
    b.run(20);
    std::vector<std::string> m = b.read();
    CHECK(has_msg(m, "STOP,VOICE_STOP"), "mic STOP -> bao STOP,VOICE_STOP");
    CHECK(!b.fsm.relay_on(), "mic STOP -> relay OFF");
  }

  // giu lau nut muc: chi chon muc, khong rot
  {
    Board b;
    b.arm();
    b.press(kLevelPins[3]);
    b.run_live(BTN_LONG_PRESS_MS + 100);
    std::vector<std::string> m = b.read();
    CHECK(has_msg(m, "BTN,4,LONG"), "giu lau -> BTN,4,LONG");
    CHECK(has_msg(m, "LVL,250"), "giu lau -> chi doi muc LVL,250");
    CHECK(!has_type(m, "START"), "giu lau -> KHONG bat dau rot");
    CHECK(b.fsm.state() == DevState::ARMED, "giu lau -> van o ARMED");
    b.release(kLevelPins[3]);
    b.run_live(60);
    m = b.read();
    CHECK(!has_type(m, "START"), "tha nut sau khi giu lau -> van khong rot");
  }

  // may tinh day danh sach muc rot xuong
  {
    Board b;
    b.arm();
    b.pc_send("CFG", "50;75;120;180;250");
    b.run(20);
    std::vector<std::string> m = b.read();
    CHECK(has_msg(m, "CFG,OK"), "CFG -> tra loi CFG,OK");
    CHECK_EQ_INT(b.fsm.preset(0), 50, "CFG cap nhat muc 1");
    CHECK_EQ_INT(b.fsm.preset(4), 250, "CFG cap nhat muc 5");
    b.tap_level(1);
    m = b.read();
    CHECK(has_msg(m, "START,75"), "nhan nut 2 sau CFG -> START,75");
  }

  // HB gui dinh ky de may tinh biet ESP con song
  {
    Board b;
    b.arm();
    b.read();
    b.run(HB_PERIOD_MS * 2 + 100);
    std::vector<std::string> m = b.read();
    int n_hb = 0;
    for (size_t i = 0; i < m.size(); ++i)
      if (m[i].compare(0, 3, "HB,") == 0) ++n_hb;
    CHECK(n_hb >= 2, "gui HB dinh ky (>= 2 lan trong ~600 ms)");
  }
}

// ---------------------------------------------------------------------------
int main() {
  printf("== UNIT TEST FIRMWARE ESP32 (may rot nuoc) ==\n");
  test_protocol();
  test_boot_and_sensor();
  test_ack_backoff();
  test_button_and_pump();
  test_safety();
  test_voice_and_config();
  printf("\nKET QUA: %d dat, %d loi\n", g_pass, g_fail);
  return g_fail == 0 ? 0 : 1;
}
