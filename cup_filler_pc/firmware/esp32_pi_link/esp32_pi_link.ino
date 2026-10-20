// ===========================================================================
//  esp32_pi_link.ino - ESP32 LÀM TAY CHÂN + AUDIO CHO RASPBERRY PI
//
//  Vẫn giữ kết cấu/giao thức cũ của máy rót nước, chỉ đổi máy chủ PC -> Pi và
//  đổi sang sơ đồ chân rev mới:
//
//    Cảm biến cốc GPIO34
//       -> ESP32 gửi INPUT_STATE + CUP_PLACED qua UART2 (961000 baud)
//       -> Pi lúc đó mới bật camera, nhận diện cốc, trả CUP_OK
//       -> ESP32 MỞ KHOÁ 3 nút mức + START + mic
//    Nút mức GPIO32/33/25 = 30/60/90%
//    START GPIO26 -> START_REQUEST; Pi trả START_FILL/PUMP_SET để chạy relay
//    Relay GPIO27, tích cực mức CAO
//    INMP441 mic -> AUDIO_CHUNK PCM lên Pi
//    Pi -> SPEAKER_AUDIO PCM xuống ESP32 -> MAX98357A -> loa
//
//  GÓI TRẠNG THÁI NGÕ VÀO (MSG=0x10) có payload đúng thứ tự yêu cầu:
//      [sensor][START][level1][level2][level3]
//  Mỗi byte = 0x01 khi cảm biến tác động/nút đang nhấn, còn lại 0x00.
//  Gói được bọc bằng khung cũ AA55/LEN/SEQ/MSG/CRC để Pi phân biệt được với
//  luồng PCM nhị phân và tự đồng bộ lại nếu dây UART có nhiễu.
//
//  An toàn tại ESP32: mất cốc, mất UART >3 s khi bơm, giữ nút >=2 s, quá ml
//  hoặc quá thời gian đều ngắt relay, không chờ Pi.
// ===========================================================================
#include <Arduino.h>

#include "pi_config.h"
#include "pi_protocol.h"
#include "pi_link.h"
#include "inputs.h"
#include "pump_pi.h"
#include "audio_duplex.h"

static PiLink g_link;
static InputBank g_inputs;
static PiPump g_pump;
static AudioDuplex g_audio;

static uint8_t g_state = proto::ST_BOOT;
static bool g_piHelloAck = false;
static bool g_cupConfirmed = false;
static bool g_piControlsPump = true;       // Pi vòng kín là mặc định
static uint8_t g_selectedLevel = DEFAULT_LEVEL_INDEX;

static uint32_t g_now = 0;
static uint32_t g_tFillStart = 0;
static uint32_t g_pulseEndMs = 0;
static uint32_t g_tLastHello = 0;
static uint32_t g_tLastStatus = 0;
static uint32_t g_tLastProgress = 0;
static uint32_t g_tLastInputState = 0;
static uint32_t g_tCupPlaced = 0;
static uint32_t g_tLastCupRetry = 0;
static bool g_waitErrorSent = false;
static bool g_linkLost = false;

// ---------------------------------------------------------------------------
//  Hàm phụ / gửi trạng thái
// ---------------------------------------------------------------------------
static uint16_t roundMl(float ml) {
  if (ml <= 0.0f) return 0;
  if (ml > 65000.0f) return 65000;
  return (uint16_t)(ml + 0.5f);
}

static bool unlocked() {
  InputSnapshot s = g_inputs.snapshot();
  return s.sensor && g_cupConfirmed && g_state != proto::ST_POURING &&
         g_state != proto::ST_FAULT;
}

static uint8_t statusFlags() {
  InputSnapshot s = g_inputs.snapshot();
  uint8_t f = 0;
  if (s.sensor) f |= proto::FL_CUP_PRESENT;
  if (g_cupConfirmed) f |= proto::FL_PC_CONFIRMED;
  if (g_pump.relayOn()) f |= proto::FL_PUMPING;
  if (g_audio.micActive() || g_audio.speakerActive()) {
    f |= proto::FL_VOICE_ACTIVE;
  }
  if (g_link.everRx() && (g_now - g_link.lastRxMs()) < LINK_TIMEOUT_MS) {
    f |= proto::FL_LINK_OK;
  }
  return f;
}

static void sendHello() {
  static const uint16_t levels[N_LEVEL_BUTTONS] = {30, 60, 90};  // % chiều cao
  g_link.hello(levels, N_LEVEL_BUTTONS, (uint16_t)(g_pump.flow() * 10.0f));
  g_tLastHello = g_now;
}

static void sendStatus() {
  g_link.status(g_state, statusFlags(), roundMl(g_pump.pouredMl()),
                roundMl(g_pump.targetMl()), roundMl(g_pump.maxMl()), g_now);
  g_tLastStatus = g_now;
}

static void sendProgress() {
  g_link.progress(g_state, roundMl(g_pump.pouredMl()),
                  roundMl(g_pump.targetMl()));
  g_tLastProgress = g_now;
}

static void sendInputStateNow() {
  InputSnapshot s = g_inputs.snapshot();
  // ĐÚNG THỨ TỰ: cảm biến, nút START, các nút chọn mức.
  g_link.inputState(s.sensor, s.start, s.level);
  g_tLastInputState = g_now;
}

static uint8_t classifyHeld(uint16_t heldMs) {
  if (heldMs >= BUTTON_ESTOP_MS) return proto::BTN_ESTOP;
  if (heldMs >= BUTTON_LONG_MS) return proto::BTN_LONG;
  return proto::BTN_SHORT;
}

static float selectedTargetMl() {
  if (g_selectedLevel >= N_LEVEL_BUTTONS) return g_pump.maxMl();
  return g_pump.maxMl() * (float)LEVEL_PERCENT[g_selectedLevel] / 100.0f;
}

static void updateLed(uint32_t now) {
#if USE_STATUS_LED
  bool on = false;
  switch (g_state) {
    case proto::ST_IDLE:    on = ((now / 800) % 2) == 0; break;
    case proto::ST_WAIT_PC: on = ((now / 120) % 2) == 0; break;
    case proto::ST_READY:   on = true; break;
    case proto::ST_POURING: on = ((now / 150) % 4) != 3; break;
    case proto::ST_DONE:    on = ((now / 600) % 2) == 0; break;
    case proto::ST_FAULT:   on = ((now / 80) % 2) == 0; break;
    default:                on = ((now / 50) % 2) == 0; break;
  }
  digitalWrite(PIN_LED, on ? HIGH : LOW);
#else
  (void)now;
#endif
}

// ---------------------------------------------------------------------------
//  Bơm
// ---------------------------------------------------------------------------
static bool startPour(uint16_t ml, uint8_t mode, uint8_t source) {
  if (g_state == proto::ST_POURING) {
    g_link.ack(proto::START_FILL, 2);
    return false;
  }
  if (!unlocked()) {
    g_link.log("KHONG BOM: chua co coc hoac Pi chua gui CUP_OK");
    g_link.ack(proto::START_FILL, 1);
    return false;
  }

  float target = (ml > 0) ? (float)ml : selectedTargetMl();
  if (target > g_pump.maxMl()) target = g_pump.maxMl();
  if (target <= 0.0f) target = g_pump.maxMl();

  g_pump.startPour(target,
                   (mode > 1) ? (uint8_t)proto::MODE_PC_CLOSED_LOOP : mode,
                   g_now);
  g_tFillStart = g_now;
  g_pulseEndMs = 0;
  g_state = proto::ST_POURING;
  g_link.fillStarted(roundMl(target), g_pump.mode(), source,
                     roundMl(g_pump.maxMl()));
  sendStatus();
  return true;
}

static void endPour(uint8_t stopReason) {
  if (g_state != proto::ST_POURING) {
    g_pump.forceOff();
    return;
  }
  uint32_t elapsed = g_now - g_tFillStart;
  if (stopReason == proto::STOP_NORMAL) g_pump.finish(PiPump::DONE_TARGET);
  else g_pump.forceOff();
  g_pulseEndMs = 0;
  g_link.fillDone(roundMl(g_pump.pouredMl()), roundMl(g_pump.targetMl()),
                  stopReason, elapsed);

  InputSnapshot s = g_inputs.snapshot();
  g_state = (s.sensor && g_cupConfirmed) ? proto::ST_DONE : proto::ST_IDLE;
  sendStatus();
}

// ---------------------------------------------------------------------------
//  Mic callback (gọi từ audio task ở core 0)
// ---------------------------------------------------------------------------
static void onMicChunk(const int16_t *samples, uint16_t n, uint8_t flags,
                       void *user) {
  (void)user;
  g_link.audioChunk(samples, n, flags);
}

// ---------------------------------------------------------------------------
//  Xử lý frame Pi -> ESP32
// ---------------------------------------------------------------------------
static void handleFrame(const proto::Frame &f) {
  const uint8_t *p = f.data;
  uint8_t n = f.len;
  g_linkLost = false;

  switch (f.msg) {
    case proto::HELLO_ACK: {
      proto::HelloAck ha;
      if (proto::parseHelloAck(p, n, ha)) g_piHelloAck = true;
      break;
    }

    case proto::CUP_OK: {
      proto::CupOk ok;
      if (!proto::parseCupOk(p, n, ok)) break;
      if (ok.max_ml > 0) g_pump.setMaxMl((float)ok.max_ml);
      if (g_inputs.snapshot().sensor) {
        g_cupConfirmed = true;
        if (g_state != proto::ST_POURING) g_state = proto::ST_READY;
        g_audio.beep(1320, 70);
      }
      g_link.ack(proto::CUP_OK, 0);
      sendStatus();
      break;
    }

    case proto::CUP_REJECT: {
      proto::CupReject rj;
      if (!proto::parseCupReject(p, n, rj)) break;
      g_cupConfirmed = false;
      if (g_inputs.snapshot().sensor) g_state = proto::ST_WAIT_PC;
      sendStatus();
      break;
    }

    case proto::SET_PRESET: {
      proto::SetPreset sp;
      if (!proto::parseSetPreset(p, n, sp)) break;
      if (sp.index < N_LEVEL_BUTTONS) {
        g_selectedLevel = sp.index;
        g_link.levelSelected(g_selectedLevel,
                             LEVEL_PERCENT[g_selectedLevel], proto::SRC_PC);
      }
      g_link.ack(proto::SET_PRESET, 0);
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
      else g_link.ack(proto::STOP_FILL, 0);
      break;
    }

    case proto::PUMP_SET: {
      proto::PumpSet ps;
      if (!proto::parsePumpSet(p, n, ps)) break;
      if (g_state != proto::ST_POURING) {
        g_link.error(4, "PUMP_SET khi chua START_FILL");
        break;
      }
      g_pump.setDuty(ps.duty_pct, g_now);
      g_pulseEndMs = ps.duration_ms ? (g_now + ps.duration_ms) : 0;
      break;
    }

    case proto::SET_MODE: {
      proto::SetMode sm;
      if (!proto::parseSetMode(p, n, sm)) break;
      const uint16_t bit = (uint16_t)(1u << 0);  // MODE_PC_CONTROLS_PUMP_BIT cũ
      if (sm.on & bit) g_piControlsPump = true;
      if (sm.off & bit) g_piControlsPump = false;
      g_link.ack(proto::SET_MODE, 0);
      break;
    }

    case proto::SET_PARAMS: {
      proto::SetParams sp;
      if (!proto::parseSetParams(p, n, sp)) break;
      if (sp.has_flow()) g_pump.setFlow(sp.flow_x10 / 10.0f);
      if (sp.has_max()) g_pump.setMaxMl((float)sp.max_ml);
      if (sp.has_to()) g_pump.setTimeoutMs((uint32_t)sp.timeout_s * 1000UL);
      g_link.ack(proto::SET_PARAMS, 0);
      break;
    }

    case piproto::SPEAKER_AUDIO: {
      piproto::SpeakerAudioView v;
      if (!piproto::parseSpeakerAudio(p, n, v)) break;
      int16_t samples[118];
      uint16_t count = (v.n > 118) ? 118 : v.n;
      for (uint16_t i = 0; i < count; i++) {
        samples[i] = piproto::speakerSample(v, i);
      }
      g_audio.pushSpeaker(samples, count);
      break;
    }

    case piproto::AUDIO_CTRL: {
      piproto::AudioCtrl ac;
      if (!piproto::parseAudioCtrl(p, n, ac)) break;
      g_audio.setMicEnabled(ac.micEnable);
      g_audio.setMicContinuous(ac.micContinuous);
      g_audio.setSpeakerEnabled(ac.speakerEnable);
      g_audio.setVolumePct(ac.volumePct);
      g_link.ack(piproto::AUDIO_CTRL, 0);
      break;
    }

    case proto::CMD: {
      proto::CmdArg ca;
      if (!proto::parseCmd(p, n, ca)) break;
      if (ca.cmd == proto::CMD_DISARM) {
        g_cupConfirmed = false;
        if (g_state == proto::ST_POURING) endPour(proto::STOP_PC_REQUEST);
        g_state = g_inputs.snapshot().sensor ? proto::ST_WAIT_PC
                                             : proto::ST_IDLE;
      } else if (ca.cmd == proto::CMD_RESET_STATE) {
        g_cupConfirmed = false;
        g_waitErrorSent = false;
        if (g_state == proto::ST_POURING) endPour(proto::STOP_PC_REQUEST);
        g_state = g_inputs.snapshot().sensor ? proto::ST_WAIT_PC
                                             : proto::ST_IDLE;
      } else if (ca.cmd == proto::CMD_SELF_TEST) {
        g_audio.beep(1000, 150);               // self-test an toàn: chỉ loa/LED
      }
      g_link.ack(proto::CMD, 0);
      break;
    }

    case proto::PING:
      g_link.pong(g_now, g_state, statusFlags());
      break;

    default:
      break;
  }
}

// ---------------------------------------------------------------------------
//  Sự kiện cảm biến / nút
// ---------------------------------------------------------------------------
static void handleInputEvents(const InputEvents &ev) {
  const uint8_t sensorBit = (uint8_t)(1u << IN_SENSOR);
  const uint8_t startBit = (uint8_t)(1u << IN_START);

  if (ev.changed) sendInputStateNow();

  // --- cảm biến cốc ---
  if (ev.pressedMask & sensorBit) {
    g_cupConfirmed = false;
    g_waitErrorSent = false;
    g_tCupPlaced = g_now;
    g_tLastCupRetry = g_now;
    g_state = proto::ST_WAIT_PC;
    g_link.cupPlaced(g_now);
    g_audio.beep(880, 60);
    sendStatus();
  }
  if (ev.releasedMask & sensorBit) {
    g_cupConfirmed = false;
    if (g_state == proto::ST_POURING) endPour(proto::STOP_CUP_REMOVED);
    g_link.cupRemoved(proto::STOP_CUP_REMOVED, g_now);
    g_state = proto::ST_IDLE;
    sendStatus();
  }

  // --- nút mức: chọn ngay ở cạnh nhấn; BUTTON_EVENT gửi khi thả để có held_ms ---
  for (uint8_t i = 0; i < N_LEVEL_BUTTONS; i++) {
    uint8_t bit = (uint8_t)(1u << (IN_LEVEL0 + i));
    if (ev.pressedMask & bit) {
      if (g_state == proto::ST_POURING) {
        g_link.log("Dang bom: bo qua nut chon muc");
      } else if (unlocked()) {
        g_selectedLevel = i;
        g_link.levelSelected(i, LEVEL_PERCENT[i], proto::SRC_BUTTON);
        g_link.presetSelected(i, roundMl(selectedTargetMl()),
                              proto::SRC_BUTTON);
        g_audio.beep((uint16_t)(700 + 200 * i), 55);
      } else {
        g_link.log("Nut muc dang KHOA: cho Pi gui CUP_OK");
      }
    }
    if (ev.releasedMask & bit) {
      uint16_t held = ev.heldMs[IN_LEVEL0 + i];
      g_link.buttonEvent(i, classifyHeld(held), held, g_state);
    }
  }

  // --- START GPIO26 ---
  if (ev.pressedMask & startBit) {
    if (g_state == proto::ST_POURING) {
      // Nhấn START lần nữa trong lúc bơm = dừng tại chỗ, không phụ thuộc Pi.
      endPour(proto::STOP_BUTTON_ESTOP);
      g_link.log("START khi dang bom -> dung bom");
    } else if (unlocked()) {
      uint8_t pct = LEVEL_PERCENT[g_selectedLevel];
      g_link.startRequest(g_selectedLevel, pct);
      g_audio.beep(1040, 70);
      if (!g_piControlsPump) {
        startPour(0, proto::MODE_ESP_OPEN_LOOP, proto::SRC_BUTTON);
      }
    } else {
      // INPUT_STATE phía trên vẫn báo START=1 cho Pi, nhưng relay không được mở.
      g_link.log("START dang KHOA: chua co CUP_OK tu Pi");
    }
  }
  if (ev.releasedMask & startBit) {
    uint16_t held = ev.heldMs[IN_START];
    g_link.buttonEvent(3, classifyHeld(held), held, g_state);  // index 3 = START
  }

  // --- giữ bất kỳ nút điều khiển nào >=2 giây: dừng khẩn cấp ---
  if (ev.estopChannel >= IN_START) {
    uint8_t idx = (ev.estopChannel == IN_START)
                      ? 3
                      : (uint8_t)(ev.estopChannel - IN_LEVEL0);
    g_link.buttonEvent(idx, proto::BTN_ESTOP, ev.heldMs[ev.estopChannel],
                       g_state);
    if (g_state == proto::ST_POURING) endPour(proto::STOP_BUTTON_ESTOP);
    g_link.log("DUNG KHAN CAP bang nut - relay da ngat");
  }
}

// ---------------------------------------------------------------------------
//  setup / loop
// ---------------------------------------------------------------------------
void setup() {
  g_pump.begin();                              // relay OFF trước mọi việc khác

#if DEBUG_SERIAL
  Serial.begin(DEBUG_BAUD);
  delay(80);
  Serial.println();
  Serial.println("== ESP32 PI LINK: sensor + buttons + relay + I2S audio ==");
  Serial.print("UART2 -> Pi: RX=");
  Serial.print(PIN_PI_UART_RX);
  Serial.print(" TX=");
  Serial.print(PIN_PI_UART_TX);
  Serial.print(" baud=");
  Serial.println(PI_UART_BAUD);
#endif

  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  g_link.begin();
  g_inputs.begin(millis());

  g_audio.setMicCallback(onMicChunk, NULL);
  if (!g_audio.begin()) {
    g_link.error(6, "I2S khoi tao loi");
#if DEBUG_SERIAL
    Serial.println("[LOI] Khong khoi tao duoc I2S mic/loa");
#endif
  }

  g_now = millis();
  g_state = proto::ST_BOOT;
  g_tLastHello = g_now;
  sendHello();
  sendInputStateNow();
  sendStatus();
  g_audio.beep(880, 80);

#if DEBUG_SERIAL
  Serial.print("I2S BCLK="); Serial.print(PIN_I2S_BCLK);
  Serial.print(" LRCLK="); Serial.print(PIN_I2S_LRCLK);
  Serial.print(" MIC_SD="); Serial.print(PIN_I2S_MIC_SD);
  Serial.print(" SPK_DIN="); Serial.println(PIN_I2S_SPK_DIN);
  Serial.println("San sang. INPUT_STATE order: sensor,start,level32,level33,level25");
#endif
}

void loop() {
  g_now = millis();
  const uint32_t now = g_now;

  g_link.poll(now, handleFrame);

  InputEvents ev;
  g_inputs.update(now, ev);
  handleInputEvents(ev);

  g_pump.update(now);

  if (g_state == proto::ST_POURING) {
    if (g_pulseEndMs && (int32_t)(now - g_pulseEndMs) >= 0) {
      g_pulseEndMs = 0;
      g_pump.setDuty(0, now);
    }
    if (g_pump.finished()) {
      uint8_t reason = proto::STOP_NORMAL;
      if (g_pump.finishResult() == PiPump::STOP_OVER_VOLUME) {
        reason = proto::STOP_OVER_VOLUME;
      } else if (g_pump.finishResult() == PiPump::STOP_TIMEOUT) {
        reason = proto::STOP_TIMEOUT;
      }
      // endPour() tự gọi finish/forceOff thêm một lần an toàn rồi gửi FILL_DONE.
      g_state = proto::ST_POURING;
      // PiPump đã tự tắt relay; dùng khối gửi kết quả riêng tại đây:
      uint32_t elapsed = now - g_tFillStart;
      g_link.fillDone(roundMl(g_pump.pouredMl()),
                      roundMl(g_pump.targetMl()), reason, elapsed);
      InputSnapshot s = g_inputs.snapshot();
      g_state = (s.sensor && g_cupConfirmed) ? proto::ST_DONE : proto::ST_IDLE;
      sendStatus();
    }
  }

  // Mất liên lạc Pi: chỉ coi là lỗi an toàn sau khi đã từng nhận frame.
  bool linkOk = g_link.everRx() && ((now - g_link.lastRxMs()) < LINK_TIMEOUT_MS);
  if (g_link.everRx() && !linkOk && !g_linkLost) {
    g_linkLost = true;
    if (g_state == proto::ST_POURING) endPour(proto::STOP_LINK_LOST);
#if DEBUG_SERIAL
    Serial.println("[UART] Mat lien lac Pi >3s");
#endif
  }

  // Chờ Pi xác nhận cốc.
  if (g_state == proto::ST_WAIT_PC && g_inputs.snapshot().sensor &&
      !g_cupConfirmed) {
    if ((now - g_tLastCupRetry) >= CUP_RETRY_MS) {
      g_tLastCupRetry = now;
      g_link.cupPlaced(now);
    }
    if (!g_waitErrorSent && g_tCupPlaced &&
        (now - g_tCupPlaced) >= WAIT_PI_TIMEOUT_MS) {
      g_waitErrorSent = true;
      g_link.error(1, "Pi khong tra loi CUP_OK");
      g_state = proto::ST_FAULT;
      sendStatus();
    }
  }

  if (g_state == proto::ST_BOOT && (now - g_tLastHello) > 300) {
    g_state = g_inputs.snapshot().sensor ? proto::ST_WAIT_PC : proto::ST_IDLE;
    if (g_state == proto::ST_WAIT_PC) {
      g_tCupPlaced = now;
      g_tLastCupRetry = now;
      g_link.cupPlaced(now);
    }
  }

  if (!g_piHelloAck && (now - g_tLastHello) >= HELLO_PERIOD_MS) sendHello();
  if ((now - g_tLastInputState) >= INPUT_STATE_PERIOD_MS) sendInputStateNow();
  if ((now - g_tLastStatus) >= STATUS_PERIOD_MS) sendStatus();
  if (g_state == proto::ST_POURING &&
      (now - g_tLastProgress) >= PROGRESS_PERIOD_MS) {
    sendProgress();
  }

  updateLed(now);
  delay(1);                                    // nhường CPU/watchdog; audio ở core 0
}
