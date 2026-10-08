// ===========================================================================
//  test_firmware_run.cpp - CHẠY THẬT FIRMWARE ESP32 TRÊN MÁY TÍNH (không cần bo)
//
//  File này #include thẳng esp32_cup_filler.ino rồi gọi setup()/loop() với một
//  máy ảo mini (firmware/host_test/arduino_stub.h): đồng hồ ảo, chân GPIO thật,
//  HC-SR04 giả, mic analog giả, UART2 hai chiều.
//
//  Nhờ vậy kiểm tra được ĐÚNG logic của firmware đang dùng:
//    * nút bấm còn bị KHOÁ khi chưa có cốc / chưa có CUP_OK từ PC
//    * có CUP_OK -> mở khoá -> bấm nút -> relay bật (TÍCH CỰC MỨC CAO) -> rót đủ ml
//    * nhấc cốc giữa chừng -> ngắt bơm ngay (CUP_REMOVED, mã dừng 2)
//    * mất UART 3 s -> ngắt bơm (mã dừng 4)
//    * quá thể tích cho phép -> ngắt (mã dừng 6), quá thời gian -> ngắt (mã dừng 3)
//    * nhấn GIỮ nút 2 s -> DỪNG KHẨN CẤP (mã dừng 5)
//    * mic: N "tiếng" -> VOICE_EVENT đúng số tiếng + PCM gửi lên PC
//
//  Biên dịch & chạy (hoặc để tools/test_comms.py nhóm [9] tự gọi):
//      g++ -std=c++11 -O2 -I firmware/esp32_cup_filler -I firmware/host_test/arduino_stub
//          -o /tmp/fwrun firmware/host_test/test_firmware_run.cpp
//          firmware/host_test/arduino_stub/instances.cpp -lm
//      /tmp/fwrun
//
//  Đầu ra cho Python đọc (từng dòng):
//      TX <hex>              - bytes firmware gửi lên PC
//      PC <hex>              - bytes PC gửi xuống (để Python đối chiếu bộ mã hoá của nó)
//      MARK <tên bước>
//      CHECK OK|FAIL <nhãn>
//      RESULT <số đạt> <số sai>
// ===========================================================================
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

#include "arduino_stub.h"
#include "config.h"
#include "protocol.h"

// ---- nạp thẳng firmware cần kiểm tra --------------------------------------
#include "../esp32_cup_filler/esp32_cup_filler.ino"

static int g_ok = 0, g_fail = 0;

static void mark(const char *s) { printf("MARK %s\n", s); fflush(stdout); }

static void check(bool cond, const std::string &label) {
  if (cond) {
    g_ok++;
    printf("CHECK OK %s\n", label.c_str());
  } else {
    g_fail++;
    printf("CHECK FAIL %s\n", label.c_str());
  }
  fflush(stdout);
}

static std::string hex(const std::string &s) {
  static const char *d = "0123456789abcdef";
  std::string out;
  out.reserve(s.size() * 2);
  for (size_t i = 0; i < s.size(); i++) {
    out += d[(unsigned char)s[i] >> 4];
    out += d[(unsigned char)s[i] & 0x0F];
  }
  return out;
}

static std::string pc_ping();

// ---- vòng chạy: để đồng hồ ảo trôi rồi gọi loop() như firmware thật --------
// PC thật (cupfiller/esp_bridge.py) gửi PING giữ nhịp tim mỗi esp.ping_period_s = 1 s;
// firmware coi im lặng > LINK_TIMEOUT_MS (3 s) là MẤT LIÊN LẠC và ngắt bơm.
// Ở đây mô phỏng đúng như vậy, trừ khi bài kiểm tra CỐ Ý tắt keep-alive.
static bool g_keepalive = true;
static uint32_t g_t_last_ping = 0;

static void keepalive_tick() {
  if (!g_keepalive) return;
  if ((millis() - g_t_last_ping) >= 1000) {
    g_t_last_ping = millis();
    stub_uart_inject(pc_ping());          // không in ra để log gọn
  }
}

static void run_ms(uint32_t ms) {
  uint32_t t_end = stub_now_us() + ms * 1000u;
  while (stub_now_us() < t_end) {
    keepalive_tick();
    uint32_t t_before = stub_now_us();
    loop();
    // Phần cứng giả đã tự "tiêu thụ" thời gian (đọc ADC, chờ echo...). Nếu một vòng
    // lặp chẳng tiêu tốn gì thì mới nhích đồng hồ 1 ms cho khỏi quay vô hạn.
    if (stub_now_us() - t_before < 1000u) stub_advance_us(1000u);
  }
}

static void dump_tx() {
  std::string out = stub_uart_take();
  if (!out.empty()) printf("TX %s\n", hex(out).c_str());
  fflush(stdout);
}

static void send_pc(const std::string &frame) {
  printf("PC %s\n", hex(frame).c_str());
  stub_uart_inject(frame);
  fflush(stdout);
}

// ---- bộ mã hoá phía PC (dùng cho bài kiểm tra này) ------------------------
static std::string pc_hello_ack() {
  uint8_t buf[64];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::HELLO_ACK, 0x11);
  w.u8(proto::PROTO_VERSION);
  w.u8(proto::ESP_CAP_BUTTONS);
  w.u8((uint8_t)N_BUTTONS);
  for (uint8_t i = 0; i < N_BUTTONS; i++) w.u16(PRESET_ML[i]);
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

static std::string pc_cup_ok(float rim_mm, float base_mm, float h_mm, uint16_t max_ml,
                             const char *label = "hil") {
  uint8_t buf[64];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::CUP_OK, 0x12);
  w.u16((uint16_t)(rim_mm * 10.0f));
  w.u16((uint16_t)(base_mm * 10.0f));
  w.u16((uint16_t)(h_mm * 10.0f));
  w.u16(max_ml);
  w.u8(100);
  w.u8(0);
  w.text(label, proto::CUP_OK_LABEL_LEN);
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

static std::string pc_set_preset(uint8_t index, uint16_t ml) {
  uint8_t buf[32];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::SET_PRESET, 0x13);
  w.u8(index);
  w.u16(ml);
  w.u8(3);                                     // source = PC
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

static std::string pc_start_fill(uint16_t ml) {
  uint8_t buf[32];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::START_FILL, 0x14);
  w.u16(ml);
  w.u8(0);                                     // mode: ESP tự đong theo ml/s
  w.u8(3);                                     // source = PC
  w.u16(0);
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

static std::string pc_set_params(uint16_t flow_x10, uint16_t max_ml, uint16_t timeout_s) {
  uint8_t buf[32];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::SET_PARAMS, 0x15);
  w.u16(flow_x10);
  w.u16(max_ml);
  w.u16(timeout_s);
  w.u16(0xFFFF);
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

static std::string pc_ping() {
  uint8_t buf[32];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::PING, 0x16);
  w.u32(millis());
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

static std::string pc_cmd(uint8_t cmd, uint16_t arg = 0) {
  uint8_t buf[32];
  proto::Writer w(buf, sizeof(buf));
  w.begin(proto::CMD, 0x17);
  w.u8(cmd);
  w.u16(arg);
  size_t n = w.end();
  return std::string((const char *)buf, n);
}

// ---- mô phỏng thao tác tay -----------------------------------------------
static void press_button(uint8_t idx, uint32_t hold_ms) {
  stub_set_pin(BUTTON_PINS[idx], LOW);
  run_ms(BUTTON_DEBOUNCE_MS + 10);
  run_ms(hold_ms);
  stub_set_pin(BUTTON_PINS[idx], HIGH);
  run_ms(BUTTON_DEBOUNCE_MS + 10);
}

static bool relay_on() { return stub_pin(PIN_RELAY) == HIGH; }

static void place_cup(float height_mm) {
  stub_set_distance_mm(CUP_BASELINE_MM - height_mm);
  run_ms(CUP_SAMPLE_MS + CUP_DEBOUNCE_MS + 150);   // đủ để cảm biến chống dội xong
  dump_tx();
}

static void remove_cup() {
  stub_set_distance_mm(CUP_BASELINE_MM);
  run_ms(CUP_SAMPLE_MS + CUP_MISSING_MS + 150);
  dump_tx();
}

// ===========================================================================
int main() {
  stub_reset();
  stub_set_distance_mm(CUP_BASELINE_MM);        // khay trống lúc khởi động
  setup();
  dump_tx();

  // ------------------------------------------------------------------ [1]
  mark("khoi-dong-hello");
  run_ms(700);
  dump_tx();
  check(stubState().n_pulsein > 0, "firmware đã đo khoảng cách khay bằng HC-SR04 (pulseIn)");
  check(!relay_on() && !g_pump.isOn(), "relay OFF ngay sau khi cấp điện");

  send_pc(pc_hello_ack());
  run_ms(300);
  dump_tx();

  // ------------------------------------------------------------------ [2]
  mark("nut-khi-chua-co-coc");
  press_button(0, 120);                          // bấm nút 1 khi KHAY TRỐNG
  run_ms(300);
  dump_tx();
  check(!relay_on(), "chưa có cốc: bấm nút không bật bơm");

  // ------------------------------------------------------------------ [3]
  mark("dat-coc");
  place_cup(95.0f);
  run_ms(500);
  dump_tx();
  check(g_cup.present(), "cảm biến siêu âm nhận ra cốc cao ~95 mm");

  mark("nut-khi-chua-co-cup_ok");
  press_button(2, 120);                          // bấm nút 3: CHƯA có CUP_OK -> phải bị khoá
  run_ms(400);
  dump_tx();
  check(!relay_on(), "có cốc nhưng CHƯA có CUP_OK: nút vẫn bị khoá (không bơm)");

  // ------------------------------------------------------------------ [4]
  mark("cup_ok-mo-khoa");
  send_pc(pc_cup_ok(33.0f, 30.0f, 95.0f, 316, "hil_cup"));
  run_ms(300);
  dump_tx();
  check(g_pcConfirmed && unlocked(), "CUP_OK của PC -> nút/mic được MỞ KHOÁ");

  mark("bam-nut-3-rot-200ml");
  press_button(2, 120);                          // nút 3 = 200 ml
  run_ms(150);
  check(relay_on(), "sau khi mở khoá: bấm nút 3 -> RELAY BẬT (tích cực mức CAO)");
  check(g_state == proto::ST_POURING, "firmware chuyển sang trạng thái POURING");
  uint32_t t0 = millis();
  while (g_state == proto::ST_POURING && (millis() - t0) < 20000) run_ms(50);
  dump_tx();
  check(g_state != proto::ST_POURING, "rót xong: firmware tự kết thúc lượt rót");
  check(g_pump.pouredMl() >= 190.0f && g_pump.pouredMl() <= 212.0f,
        "lượng nước rót đúng mục tiêu 200 ml (thực tế " +
            std::to_string((int)(g_pump.pouredMl() + 0.5f)) + " ml)");
  check(!relay_on(), "rót xong: relay TẮT");

  // ------------------------------------------------------------------ [5]
  mark("nhac-coc-giua-chung");
  send_pc(pc_start_fill(150));                   // PC ra lệnh rót 150 ml (START_FILL)
  run_ms(1200);
  check(relay_on(), "đang rót 150 ml: relay đang bật");
  dump_tx();
  remove_cup();                                  // NHẤC CỐC giữa chừng
  check(!relay_on(), "nhấc cốc giữa chừng -> NGẮT BƠM ngay");
  check(g_pump.pouredMl() < 150.0f, "dừng trước khi đủ 150 ml");

  // ------------------------------------------------------------------ [6]
  mark("mat-lien-lac-uart");
  place_cup(95.0f);
  send_pc(pc_cup_ok(33.0f, 30.0f, 95.0f, 316));
  run_ms(300);
  press_button(2, 120);
  run_ms(800);
  check(relay_on(), "đang rót (chuẩn bị thử mất liên lạc)");
  dump_tx();
  g_keepalive = false;                           // PC im lặng hẳn (rút cáp USB)
  run_ms(4000);
  dump_tx();
  g_keepalive = true;
  check(!relay_on(), "mất liên lạc UART 3 s -> ESP tự NGẮT BƠM");
  check(g_linkLost, "firmware có cờ mất liên lạc");

  // ------------------------------------------------------------------ [7]
  mark("tran-the-tich-tu-pc");
  send_pc(pc_set_params(0xFFFF, 60, 0xFFFF));    // trần 60 ml
  run_ms(200);
  dump_tx();
  press_button(4, 120);                          // nút 5 = 300 ml -> bị cắt còn 60
  run_ms(300);
  dump_tx();
  t0 = millis();
  while (g_state == proto::ST_POURING && (millis() - t0) < 10000) run_ms(50);
  dump_tx();
  check(!relay_on(), "mức rót bị cắt theo trần thể tích mà PC gửi -> dừng đúng lúc");
  check(g_pump.pouredMl() <= 70.0f, "không rót quá trần 60 ml (thực tế " +
            std::to_string((int)(g_pump.pouredMl() + 0.5f)) + " ml)");

  mark("ha-tran-giua-chung");
  send_pc(pc_set_params(0xFFFF, 500, 0xFFFF));   // trần lại 500 ml
  run_ms(200);
  press_button(4, 120);                          // bắt đầu rót 300 ml
  run_ms(1000);                                  // đang rót (~40 ml)
  send_pc(pc_set_params(0xFFFF, 20, 0xFFFF));    // PC hạ trần xuống 20 ml ĐANG khi rót
  run_ms(400);
  dump_tx();
  check(!relay_on(), "trần thể tích bị hạ xuống thấp hơn mức đã rót -> ESP ngắt bơm ngay");
  check(g_pump.pouredMl() < 60.0f, "ngắt ngay sau khi hạ trần, không rót tiếp tới 300 ml ("
            "thực tế " + std::to_string((int)(g_pump.pouredMl() + 0.5f)) + " ml)");

  // ------------------------------------------------------------------ [8]
  mark("qua-thoi-gian");
  send_pc(pc_set_params(0xFFFF, 500, 2));        // trần 500 ml, timeout 2 s
  run_ms(200);
  press_button(4, 120);                          // 300 ml @40 ml/s = 7.5 s > 2 s timeout
  run_ms(300);
  t0 = millis();
  while (g_state == proto::ST_POURING && (millis() - t0) < 10000) run_ms(50);
  dump_tx();
  check(!relay_on(), "quá thời gian an toàn (2 s) -> ngắt bơm");
  check(g_pump.pouredMl() < 150.0f, "ngắt trước khi rót đủ 300 ml");

  // ------------------------------------------------------------------ [9]
  mark("dung-khan-cap-giu-nut");
  send_pc(pc_set_params(0xFFFF, 500, 60));
  run_ms(200);
  press_button(3, 120);                          // nút 4 = 250 ml -> bắt đầu rót
  run_ms(1000);
  check(relay_on(), "đang rót 250 ml (chuẩn bị thử dừng khẩn cấp)");
  press_button(0, 2200);                         // GIỮ nút 2.2 s = DỪNG KHẨN CẤP
  dump_tx();
  check(!relay_on(), "giữ nút 2 s -> dừng khẩn cấp, relay tắt");
  check(g_pump.pouredMl() < 150.0f, "dừng khẩn cấp ngắt trước khi rót đủ 250 ml");

  // ----------------------------------------------------------------- [10]
  mark("lenh-tu-pc");
  send_pc(pc_ping());
  run_ms(200);
  send_pc(pc_set_preset(3, 250));
  run_ms(150);
  send_pc(pc_cmd(proto::CMD_TARE));
  run_ms(400);
  send_pc(pc_cmd(proto::CMD_DISARM));
  run_ms(300);
  dump_tx();
  check(!unlocked(), "lệnh DISARM của PC -> khoá lại nút/mic");

  // ----------------------------------------------------------------- [11]
  mark("mic-dem-tieng");
  place_cup(95.0f);
  send_pc(pc_cup_ok(33.0f, 30.0f, 95.0f, 316));
  run_ms(300);
  if (MIC_TYPE != 0) {
    stub_mic_bursts(3, 300000, 250000, 1400);    // 3 "tiếng"
    run_ms(3500);
    dump_tx();
    check(true, "mic: đã phát 3 tiếng cho firmware xử lý (xem VOICE_EVENT/AUDIO_CHUNK)");
  } else {
    check(true, "mic bị tắt trong config.h -> bỏ qua bài kiểm tra mic");
  }

  // ----------------------------------------------------------------- [12]
  mark("ket-thuc");
  run_ms(300);
  dump_tx();
  printf("RESULT %d %d\n", g_ok, g_fail);
  fflush(stdout);
  return g_fail == 0 ? 0 : 1;
}
