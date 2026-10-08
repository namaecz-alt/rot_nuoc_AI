// ===========================================================================
//  esp32_cup_filler.ino  -  BO ĐIỀU KHIỂN MÁY RÓT NƯỚC (ESP32)
//
//  Vai trò: CẢM BIẾN + NÚT BẤM + MIC + RELAY. Nhận diện cốc do MÁY TÍNH làm.
//
//  LUỒNG HOẠT ĐỘNG (đúng theo yêu cầu thiết kế):
//
//     1. Đặt cốc vào cảm biến
//            ESP32 -> CUP_PLACED (UART) -> PC
//     2. PC lúc này mới BẬT CAMERA + nhận diện cốc
//            PC -> CUP_OK (kèm hình học cốc, thể tích rót được tối đa)
//     3. ESP32 nhận CUP_OK -> MỞ KHOÁ nút bấm và mic   (trước đó bấm/nói vô hiệu)
//     4. Người dùng:
//            - NHẤN NÚT chọn mức   (nút TÍCH CỰC MỨC THẤP, chân INPUT_PULLUP)
//            - hoặc NÓI vào mic    (PCM gửi lên PC, hoặc ESP tự đếm "tiếng")
//        -> relay TÍCH CỰC MỨC CAO đóng -> BƠM NƯỚC VÀO CỐC
//     5. Nhấc cốc ra -> ESP ngắt bơm ngay + báo CUP_REMOVED; PC đóng camera
//
//  An toàn tại chỗ (không phụ thuộc PC): quá thể tích, quá thời gian, mất cốc,
//  mất liên lạc UART, nhấn giữ nút 2 giây -> NGẮT BƠM.
//
//  Sơ đồ chân & cách nạp: firmware/README.md   -   giao thức: docs/PROTOCOL.md
// ===========================================================================
#include <Arduino.h>

#include "config.h"
#include "protocol.h"
#include "cup_sensor.h"
#include "buttons.h"
#include "pump.h"
#include "voice_mic.h"

// ---------------------------------------------------------------------------
//  Cổng UART nối máy tính (UART2) + cổng USB để xem log
// ---------------------------------------------------------------------------
HardwareSerial &PC = Serial2;

static proto::Decoder g_dec;
static proto::Frame g_frame;
static uint8_t g_tx[320];
static uint8_t g_seq = 0;
static uint8_t g_audioSeq = 0;

static CupSensor g_cup;
static ButtonBank g_btn;
static PumpDriver g_pump;
static VoiceEngine g_voice;

static uint8_t g_state = proto::ST_BOOT;
static bool g_pcHelloAck = false;
static bool g_pcConfirmed = false;      // PC đã nhận diện cốc -> mở khoá nút/mic
static bool g_hasCupOk = false;
static proto::CupOk g_cupOk;
static bool g_pcControlsPump = false;   // PC điều khiển relay (vòng kín bằng thị giác)
static char g_lastError[40] = {0};

static uint16_t g_presetIndex = PRESET_INDEX_DEFAULT;
static uint16_t g_presetMl = PRESET_ML[PRESET_INDEX_DEFAULT];
static uint32_t g_tFillStart = 0;

static uint32_t g_lastRxMs = 0;         // 0 = chưa từng nhận gói nào từ PC
static bool g_linkLost = false;
static uint32_t g_tLastHello = 0;
static uint32_t g_tLastStatus = 0;
static uint32_t g_tLastProgress = 0;
static uint32_t g_tWaitPc = 0;
static uint32_t g_tLastCupRetry = 0;
static uint32_t g_pulseEndMs = 0;       // PUMP_SET có duration_ms -> hết hạn thì tắt

// ---------------------------------------------------------------------------
//  Gửi gói tin
// ---------------------------------------------------------------------------
static uint8_t nextSeq() {
  g_seq++;
  return g_seq;
}

static void sendRaw(size_t len) {
  if (len) PC.write(g_tx, len);
}

static void logMsg(const char *text) {
#if DEBUG_SERIAL
  Serial.println(text);
#endif
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encLog(w, nextSeq(), text));
}

static void sendHello() {
  uint8_t caps = proto::ESP_CAP_BUTTONS | proto::ESP_CAP_US_SENSOR | proto::ESP_CAP_FLOW_CAL;
  if (MIC_TYPE != 0) caps |= proto::ESP_CAP_VOICE;
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encHello(w, nextSeq(), FW_VERSION_MAJOR, caps, PRESET_ML, N_BUTTONS,
                          (uint16_t)(g_pump.flow() * 10.0f)));
  g_tLastHello = millis();
}

static uint8_t statusFlags() {
  uint32_t now = millis();
  uint8_t f = 0;
  if (g_cup.present()) f |= proto::FL_CUP_PRESENT;
  if (g_pcConfirmed) f |= proto::FL_PC_CONFIRMED;
  if (g_pump.isOn()) f |= proto::FL_PUMPING;
  if (MIC_TYPE != 0) f |= proto::FL_VOICE_ACTIVE;
  if (g_lastRxMs && (now - g_lastRxMs) < LINK_TIMEOUT_MS) f |= proto::FL_LINK_OK;
  if (g_state == proto::ST_MANUAL)   f |= proto::FL_MANUAL;
  return f;
}

static void sendStatus() {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encStatus(w, nextSeq(), g_state, statusFlags(),
                           (uint16_t)(g_pump.pouredMl() + 0.5f),
                           (uint16_t)(g_pump.targetMl() + 0.5f),
                           (uint16_t)(g_pump.maxMl() + 0.5f), millis()));
  g_tLastStatus = millis();
}

static void sendProgress() {
  uint16_t poured = (uint16_t)(g_pump.pouredMl() + 0.5f);
  uint16_t target = (uint16_t)(g_pump.targetMl() + 0.5f);
  uint16_t pct = target ? (uint16_t)((100UL * poured) / target) : 0;
  if (pct > 200) pct = 200;
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encFillProgress(w, nextSeq(), poured, (uint8_t)pct, target, g_state));
  g_tLastProgress = millis();
}

static void sendCupPlaced(uint8_t flags) {
  float dist = g_cup.lastDistance();
  float base = g_cup.baseline();
  float h = g_cup.cupHeight();
  if (dist < 0) dist = 0;
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encCupPlaced(w, nextSeq(), (uint16_t)(dist + 0.5f), (uint16_t)(base + 0.5f),
                              (uint16_t)(h + 0.5f), flags, millis()));
  g_tLastCupRetry = millis();
}

static void sendCupRemoved() {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encCupRemoved(w, nextSeq(), proto::STOP_CUP_REMOVED, millis()));
}

static void sendPresetSelected(uint8_t source) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encPresetSelected(w, nextSeq(), (uint8_t)g_presetIndex, g_presetMl, source));
}

static void sendFillStarted(uint8_t source) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encFillStarted(w, nextSeq(), (uint16_t)(g_pump.targetMl() + 0.5f),
                                g_pump.mode(), source, (uint16_t)(g_pump.maxMl() + 0.5f)));
}

static void sendFillDone(uint8_t status, uint32_t elapsedMs) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encFillDone(w, nextSeq(), (uint16_t)(g_pump.pouredMl() + 0.5f),
                             (uint16_t)(g_pump.targetMl() + 0.5f), status, elapsedMs));
}

static void sendButtonEvent(uint8_t index, uint8_t event, uint16_t pressMs) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encButtonEvent(w, nextSeq(), index, event, pressMs, g_state));
}

static void sendVoiceEvent(uint8_t kind, uint8_t index, uint8_t conf, uint8_t peaks,
                           uint16_t rms, uint16_t durMs) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encVoiceEvent(w, nextSeq(), kind, index, conf, peaks, rms, durMs));
}

static void sendError(uint8_t code, const char *text) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encError(w, nextSeq(), code, text));
}

static void sendAck(uint8_t ackedMsg, uint8_t status) {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encAck(w, nextSeq(), ackedMsg, status));
}

static void sendPong() {
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encPong(w, nextSeq(), millis(), g_state, statusFlags()));
}

// ---------------------------------------------------------------------------
//  LED báo trạng thái
// ---------------------------------------------------------------------------
static void updateLed(uint32_t now) {
#if USE_STATUS_LED
  bool on;
  switch (g_state) {
    case proto::ST_IDLE:    on = ((now / 800) % 2) == 0; break;   // nháy chậm: chờ đặt cốc
    case proto::ST_WAIT_PC: on = ((now / 120) % 2) == 0; break;   // nháy nhanh: chờ PC
    case proto::ST_READY:   on = true; break;                    // sáng đều: đã mở khoá
    case proto::ST_POURING: on = ((now / 150) % 4) != 3; break;   // nháy gấp: ĐANG BƠM
    case proto::ST_DONE:    on = ((now / 600) % 2) == 0; break;   // rót xong
    case proto::ST_MANUAL:  on = ((now / 400) % 2) == 0; break;
    case proto::ST_FAULT:   on = ((now / 80) % 2) == 0; break;    // nháy rất nhanh: LỖI
    default:                on = ((now / 50) % 2) == 0; break;    // BOOT
  }
  digitalWrite(PIN_LED, on ? HIGH : LOW);
#else
  (void)now;
#endif
}

// ---------------------------------------------------------------------------
//  Bơm
// ---------------------------------------------------------------------------
static bool unlocked() {
  return (g_state == proto::ST_MANUAL) || (g_pcConfirmed && g_cup.present());
}

static bool startPour(uint16_t ml, uint8_t mode, uint8_t source) {
  if (g_state == proto::ST_POURING) {
    logMsg("DANG BOM ROI - bo qua lenh moi");
    sendAck(proto::START_FILL, 2);
    return false;
  }
  if (!unlocked()) {
    logMsg("KHONG BOM: phai dat coc va cho PC xac nhan truoc");
    sendAck(proto::START_FILL, 1);
    return false;
  }
  float target = (float)ml;
  if (target <= 0.0f) target = (float)g_presetMl;
  if (target > g_pump.maxMl()) {
    target = g_pump.maxMl();
    logMsg("Giam muc nuoc cho vua the tich coc");
  }
  if (mode > 1) mode = 0;
  g_pump.startPour(target, mode, millis());
  g_tFillStart = millis();
  g_pulseEndMs = 0;
  g_state = proto::ST_POURING;
  sendFillStarted(source);
  sendStatus();
  logMsg("BAT DAU BOM");
  return true;
}

// Kết thúc lượt rót: ngắt relay, gửi FILL_DONE, chuyển trạng thái
static void endPour(uint8_t stopReason) {
  if (g_state != proto::ST_POURING) return;
  uint32_t elapsed = millis() - g_tFillStart;
  if (stopReason == proto::STOP_NORMAL) g_pump.finish(PumpDriver::DONE_TARGET);
  else                                  g_pump.forceOff();
  g_pulseEndMs = 0;
  sendFillDone(stopReason, elapsed);
  g_state = (g_cup.present() && g_pcConfirmed) ? proto::ST_DONE : proto::ST_IDLE;
#if DEBUG_SERIAL
  Serial.print("KET THUC BOM: ");
  Serial.print(g_pump.pouredMl(), 1);
  Serial.print(" / ");
  Serial.print(g_pump.targetMl(), 1);
  Serial.print(" ml, ma dung ");
  Serial.println(stopReason);
#endif
  sendStatus();
}

// ---------------------------------------------------------------------------
//  Chọn mức nước (nút bấm / mic / PC)
// ---------------------------------------------------------------------------
static void selectPreset(uint8_t index, uint8_t source, bool startNow) {
  if (index >= N_BUTTONS) return;
  if (g_state == proto::ST_POURING) {
    logMsg("Dang bom - bo qua thao tac chon muc");
    return;
  }
  if (!unlocked()) {
    logMsg("NUT/MIC BI KHOA: chua co CUP_OK tu PC");
    return;
  }
  g_presetIndex = index;
  g_presetMl = PRESET_ML[index];
  sendPresetSelected(source);
#if DEBUG_SERIAL
  Serial.print("Chon muc ");
  Serial.print(g_presetMl);
  Serial.println(" ml");
#endif
  if (!startNow) return;
  if (g_pcControlsPump) {
    logMsg("Cho lenh bom tu PC (che do PC dieu khien bom)");
  } else {
    startPour(g_presetMl, proto::MODE_ESP_OPEN_LOOP, source);
  }
}

// ---------------------------------------------------------------------------
//  Xử lý gói tin nhận từ PC
// ---------------------------------------------------------------------------
static void handleFrame(const proto::Frame &f) {
  const uint8_t *p = f.data;
  uint8_t n = f.len;
  g_lastRxMs = millis();
  g_linkLost = false;

  switch (f.msg) {
    case proto::HELLO_ACK: {
      proto::HelloAck ha;
      if (proto::parseHelloAck(p, n, ha)) {
        g_pcHelloAck = true;
#if DEBUG_SERIAL
        Serial.print("PC da san sang (HELLO_ACK v");
        Serial.print(ha.version);
        Serial.print(", caps 0x");
        Serial.print(ha.caps, HEX);
        Serial.println(")");
#endif
      }
      break;
    }
    case proto::CUP_OK: {
      proto::CupOk ok;
      if (!proto::parseCupOk(p, n, ok)) break;
      g_cupOk = ok;
      g_hasCupOk = true;
      g_pcConfirmed = true;
      if (ok.max_ml > 0) {
        float m = (float)ok.max_ml;
        if (m > (float)MAX_FILL_ML) m = (float)MAX_FILL_ML;
        g_pump.setMaxMl(m);
      }
      if (g_cup.present()) g_state = proto::ST_READY;
#if DEBUG_SERIAL
      Serial.print("PC XAC NHAN COC: cao ");
      Serial.print(ok.height_mm(), 1);
      Serial.print(" mm, rot toi da ");
      Serial.print(ok.max_ml);
      Serial.print(" ml -> MO KHOA NUT/MIC");
      Serial.println();
#endif
      sendAck(proto::CUP_OK, 0);
      sendStatus();
      break;
    }
    case proto::CUP_REJECT: {
      proto::CupReject rj;
      if (!proto::parseCupReject(p, n, rj)) break;
      g_pcConfirmed = false;
      g_hasCupOk = false;
      if (g_cup.present()) g_state = proto::ST_WAIT_PC;
      strncpy(g_lastError, rj.text, sizeof(g_lastError) - 1);
      logMsg("PC chua nhan dien duoc coc - hay dat lai coc");
      sendStatus();
      break;
    }
    case proto::SET_PRESET: {
      proto::SetPreset sp;
      if (!proto::parseSetPreset(p, n, sp)) break;
      if (sp.index < N_BUTTONS) g_presetIndex = sp.index;
      g_presetMl = sp.ml ? sp.ml : PRESET_ML[g_presetIndex];
      sendAck(proto::SET_PRESET, 0);
      break;
    }
    case proto::START_FILL: {
      proto::StartFill sf;
      if (!proto::parseStartFill(p, n, sf)) break;
      if (sf.timeout_ms) g_pump.setTimeoutMs(sf.timeout_ms);
      startPour(sf.ml, sf.mode, sf.source);
      break;
    }
    case proto::STOP_FILL: {
      proto::StopFill st;
      uint8_t reason = proto::STOP_PC_REQUEST;
      if (proto::parseStopFill(p, n, st)) reason = st.reason;
      if (g_state == proto::ST_POURING) endPour(reason);
      else sendAck(proto::STOP_FILL, 0);
      break;
    }
    case proto::PUMP_SET: {
      proto::PumpSet ps;
      if (!proto::parsePumpSet(p, n, ps)) break;
      if (g_state != proto::ST_POURING) {
        sendError(4, "PUMP_SET khi khong bom");
        break;
      }
      g_pump.setDuty(ps.duty_pct, millis());
      // duration_ms > 0 = xung có thời hạn, hết hạn thì trả duty về 0
      g_pulseEndMs = ps.duration_ms ? (millis() + ps.duration_ms) : 0;
      break;
    }
    case proto::SET_MODE: {
      proto::SetMode sm;
      if (!proto::parseSetMode(p, n, sm)) break;
      uint16_t bit = (uint16_t)(1 << MODE_PC_CONTROLS_PUMP_BIT);
      if (sm.on & bit)  g_pcControlsPump = true;
      if (sm.off & bit) g_pcControlsPump = false;
      logMsg(g_pcControlsPump ? "Che do: PC dieu khien bom"
                              : "Che do: ESP tu dong bom");
      sendAck(proto::SET_MODE, 0);
      break;
    }
    case proto::SET_PARAMS: {
      proto::SetParams sp;
      if (!proto::parseSetParams(p, n, sp)) break;
      if (sp.has_flow()) g_pump.setFlow(sp.flow_x10 / 10.0f);
      if (sp.has_max())  g_pump.setMaxMl((float)sp.max_ml);
      if (sp.has_to())   g_pump.setTimeoutMs((uint32_t)sp.timeout_s * 1000UL);
      sendAck(proto::SET_PARAMS, 0);
      logMsg("Da cap nhat hieu chuan tu PC");
      break;
    }
    case proto::CMD: {
      proto::CmdArg ca;
      if (!proto::parseCmd(p, n, ca)) break;
      switch (ca.cmd) {
        case proto::CMD_TARE: {
          if (g_cup.present()) logMsg("CANH BAO: dang co coc - hay nhac coc ra truoc khi tare");
          float b = g_cup.tare(millis());
#if DEBUG_SERIAL
          Serial.print("Tare xong: mat khay ");
          Serial.print(b, 1);
          Serial.println(" mm");
#endif
          break;
        }
        case proto::CMD_DISARM:
          g_pcConfirmed = false;
          g_hasCupOk = false;
          if (g_state == proto::ST_POURING) endPour(proto::STOP_PC_REQUEST);
          g_state = g_cup.present() ? proto::ST_WAIT_PC : proto::ST_IDLE;
          break;
        case proto::CMD_RESET_STATE:
          g_pcConfirmed = false;
          g_hasCupOk = false;
          g_tWaitPc = 0;
          g_state = g_cup.present() ? proto::ST_WAIT_PC : proto::ST_IDLE;
          break;
        case proto::CMD_SELF_TEST:
          logMsg("self-test: bat relay 300 ms");
          digitalWrite(PIN_RELAY, RELAY_ACTIVE_HIGH ? HIGH : LOW);
          delay(300);
          digitalWrite(PIN_RELAY, RELAY_ACTIVE_HIGH ? LOW : HIGH);
          break;
        case proto::CMD_SAVE_CAL:
          logMsg("luu hieu chuan vao NVS: chua ho tro trong ban nay");
          break;
        default:
          logMsg("arm");
          break;
      }
      sendAck(proto::CMD, 0);
      break;
    }
    case proto::PING:
      sendPong();
      break;
    default:
      break;
  }
}

// ---------------------------------------------------------------------------
//  Callback từ mic
// ---------------------------------------------------------------------------
static void onAudioChunk(const int16_t *samples, uint16_t n, uint8_t flags, void *user);
static void onVoiceSegment(uint16_t nPeaks, uint16_t rms, uint16_t durMs, void *user);
static void selectPreset(uint8_t index, uint8_t source, bool startNow);

static void onAudioChunk(const int16_t *samples, uint16_t n, uint8_t flags, void *user) {
  (void)user;
#if VOICE_MODE == 2
  if (flags & proto::AU_START) g_audioSeq = 0;
  proto::Writer w(g_tx, sizeof(g_tx));
  sendRaw(proto::encAudioChunk(w, nextSeq(), g_audioSeq++, flags, samples, n));
#else
  (void)samples; (void)n; (void)flags;
#endif
}

static void onVoiceSegment(uint16_t nPeaks, uint16_t rms, uint16_t durMs, void *user) {
  (void)user;
  uint8_t idx  = (nPeaks > 0) ? (uint8_t)(nPeaks - 1) : 0;
  uint32_t conf = (uint32_t)(rms / 40);
  if (conf > 100) conf = 100;
  if (idx >= N_BUTTONS) idx = (uint8_t)(N_BUTTONS - 1);
  sendVoiceEvent(proto::VOICE_CLAPS, idx, (uint8_t)conf, (uint8_t)nPeaks, rms, durMs);
#if DEBUG_SERIAL
  Serial.print("Mic: ");
  Serial.print(nPeaks);
  Serial.print(" tieng, rms ");
  Serial.print(rms);
  Serial.print(", ");
  Serial.print(durMs);
  Serial.println(" ms");
#endif
#if VOICE_MODE == 1
  // ESP tự chọn mức + bơm (không cần PC nhận dạng)
  if (nPeaks >= VOICE_MIN_PEAKS) {
    selectPreset(idx, proto::SRC_VOICE, VOICE_AUTO_START && !g_pcControlsPump);
  }
#endif
}

// ---------------------------------------------------------------------------
//  Đọc UART (không chặn, có hạn mức mỗi vòng)
// ---------------------------------------------------------------------------
static void pollUart() {
  uint16_t budget = UART_RX_BUDGET;
  while (budget-- > 0 && PC.available() > 0) {
    if (g_dec.push((uint8_t)PC.read())) {
      while (g_dec.pop(g_frame)) handleFrame(g_frame);   // xử lý ngay, không để tràn hàng đợi
    }
  }
  while (g_dec.pop(g_frame)) handleFrame(g_frame);
}

// ---------------------------------------------------------------------------
//  Sự kiện cảm biến cốc
// ---------------------------------------------------------------------------
static void handleCupEvent(bool present) {
  if (present) {
    g_tWaitPc = millis();
    g_state = proto::ST_WAIT_PC;
    g_pcConfirmed = false;
    g_hasCupOk = false;
    sendCupPlaced(0x01);
#if DEBUG_SERIAL
    Serial.print("DA DAT COC: cao ");
    Serial.print(g_cup.cupHeight(), 1);
    Serial.println(" mm -> bao PC bat camera & nhan dien");
#endif
  } else {
    g_pcConfirmed = false;
    g_hasCupOk = false;
    if (g_state == proto::ST_POURING) {
      endPour(proto::STOP_CUP_REMOVED);
      logMsg("MAT COC -> NGAT BOM");
    }
    g_hasCupOk = false;
    sendCupRemoved();
    g_tWaitPc = 0;
    g_state = proto::ST_IDLE;
#if DEBUG_SERIAL
    Serial.println("DA NHAC COC");
#endif
  }
  sendStatus();
}

// ---------------------------------------------------------------------------
//  setup / loop
// ---------------------------------------------------------------------------
void setup() {
#if DEBUG_SERIAL
  Serial.begin(115200);
  delay(80);
  Serial.println();
  Serial.println("== MAY ROT NUOC - BO DIEU KHIEN ESP32 ==");
  Serial.print("Phien ban firmware: ");
  Serial.print(FW_VERSION_MAJOR);
  Serial.print(".");
  Serial.println(FW_VERSION_MINOR);
#endif
  PC.setRxBufferSize(2048);
  PC.setTxBufferSize(2048);
  PC.begin(UART_BAUD, SERIAL_8N1, PIN_UART_RX, PIN_UART_TX);

  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  g_pump.begin();                       // relay về OFF trước tiên (an toàn)
  g_btn.begin();
  g_cup.begin(CUP_BASELINE_MM);
  g_voice.setCallbacks(onAudioChunk, onVoiceSegment, NULL);
  g_voice.begin();

  g_state = proto::ST_BOOT;
  g_tLastHello = millis();
  sendHello();
#if DEBUG_SERIAL
  Serial.print("Baseline khay: ");
  Serial.print(g_cup.baseline(), 1);
  Serial.println(" mm");
  if (g_voice.enabled()) {
    Serial.print("Mic: tan so lay mau thuc te ");
    Serial.print(g_voice.sampleRate());
    Serial.println(" Hz");
  }
  Serial.println("San sang - cho dat coc...");
#endif
}

void loop() {
  uint32_t now = millis();

  pollUart();
  g_cup.update(now);
  g_voice.update(now);
  g_pump.update(now);                 // băm relay theo duty + cộng dồn ml + an toàn

  // ---- nút bấm -----------------------------------------------------------
  uint8_t btnIdx = 0xFF;
  uint16_t btnHeld = 0;
  ButtonBank::Event ev = g_btn.update(now, btnIdx, btnHeld);
  if (ev != ButtonBank::EV_NONE && btnIdx != 0xFF) {
    sendButtonEvent(btnIdx, (uint8_t)(ev - 1), btnHeld);
    if (ev == ButtonBank::EV_ESTOP) {
      logMsg("DUNG KHAN CAP (giu nut 2 giay)");
      if (g_state == proto::ST_POURING) {
        endPour(proto::STOP_BUTTON_ESTOP);
      }
    } else if (ev == ButtonBank::EV_SHORT) {
      selectPreset(btnIdx, proto::SRC_BUTTON, BUTTON_STARTS_POUR);
    } else {                                    // EV_LONG: chỉ chọn mức, chưa bơm
      selectPreset(btnIdx, proto::SRC_BUTTON, false);
      logMsg("Da chon muc (giu lau) - bam ngan de bom");
    }
  }

  // ---- sự kiện cảm biến cốc ---------------------------------------------
  bool cupNow = false;
  if (g_cup.takeEvent(cupNow)) handleCupEvent(cupNow);

  // ---- bơm tự kết thúc (đủ ml / quá thể tích / quá thời gian) ------------
  if (g_state == proto::ST_POURING) {
    if (g_pulseEndMs && now >= g_pulseEndMs) {   // xung PUMP_SET hết hạn
      g_pulseEndMs = 0;
      g_pump.setDuty(0, now);
    }
    if (g_pump.finished()) {
      uint8_t r = g_pump.finishResult();
      uint8_t reason = proto::STOP_NORMAL;
      if (r == PumpDriver::STOP_OVER_VOLUME)    reason = proto::STOP_OVER_VOLUME;
      else if (r == PumpDriver::STOP_TIMEOUT)   reason = proto::STOP_TIMEOUT;
      endPour(reason);
    }
  }

  // ---- mất liên lạc với PC ----------------------------------------------
  bool linkOk = (g_lastRxMs != 0) && ((now - g_lastRxMs) < LINK_TIMEOUT_MS);
  if (!linkOk && g_lastRxMs != 0 && !g_linkLost) {
    g_linkLost = true;
    logMsg("MAT LIEN LAC VOI PC");
#if LINK_STOPS_PUMP
    if (g_state == proto::ST_POURING) endPour(proto::STOP_LINK_LOST);
#endif
    if (g_state == proto::ST_POURING) g_state = proto::ST_READY;
#if ALLOW_MANUAL_WHEN_LINK_LOST
    if (g_cup.present() &&
        (g_state == proto::ST_WAIT_PC || g_state == proto::ST_READY)) {
      g_state = proto::ST_MANUAL;
      logMsg("Che do du phong: nut bam van dung duoc");
    }
#endif
  }

  // ---- chờ PC xác nhận cốc quá lâu --------------------------------------
  if (g_state == proto::ST_WAIT_PC && g_cup.present() && !g_pcConfirmed) {
    if ((now - g_tLastCupRetry) >= CUP_RETRY_MS) sendCupPlaced(0x02);   // nhắc lại
    if (g_tWaitPc && (now - g_tWaitPc) >= WAIT_PC_TIMEOUT_MS) {
      g_tWaitPc = 0;
      strncpy(g_lastError, "PC_khong_tra_loi_CUP_OK", sizeof(g_lastError) - 1);
      sendError(1, "PC_khong_tra_loi_CUP_OK");
      logMsg("PC khong tra loi CUP_OK");
#if ALLOW_MANUAL_WHEN_LINK_LOST
      g_state = proto::ST_MANUAL;
#else
      g_state = proto::ST_FAULT;
#endif
    }
  }

  // ---- gói định kỳ -------------------------------------------------------
  if (g_state == proto::ST_BOOT && (now - g_tLastHello) > 300) {
    g_state = proto::ST_IDLE;                 // khởi động xong, chờ đặt cốc
  }
  if (!g_pcHelloAck && (now - g_tLastHello) >= HELLO_PERIOD_MS) sendHello();
  if ((now - g_tLastStatus) >= STATUS_PERIOD_MS) sendStatus();
  if (g_state == proto::ST_POURING && (now - g_tLastProgress) >= PROGRESS_PERIOD_MS) {
    sendProgress();
  }

  updateLed(now);
#if VOICE_MODE == 2
  if (PC.availableForWrite() < 64) delay(1);   // nhường chút thời gian cho UART
#endif
}
