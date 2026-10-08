// ===========================================================================
//  serial_link.h - GIAO TIẾP ESP32 <-> MÁY TÍNH QUA SERIAL
//                  (UART2 GPIO16/17 qua mạch USB-TTL, HOẶC cáp USB của board)
//
//  ĐÂY LÀ FILE RIÊNG CHO "PHẦN NÓI CHUYỆN VỚI PC" (viết để mở bằng VS Code /
//  PlatformIO như các file .h khác của firmware). Toàn bộ đường truyền nằm ở đây,
//  esp32_cup_filler.ino chỉ còn logic của máy rót nước:
//
//     CHỌN CỔNG   config.h -> UART_USE_USB_SERIAL
//                    0 = Serial2  (GPIO16/17)  -> cần mạch USB-TTL cắm vào PC
//                    1 = Serial   (cổng USB có sẵn trên board DevKit V1)
//                                 -> KHÔNG cần mạch USB-TTL, chỉ cần cáp USB
//     MỞ CỔNG     begin(): đặt đệm RX/TX 2048 byte, UART_BAUD, chân RX/TX
//     GỬI         mỗi tin được đóng khung AA 55 + LEN + SEQ + MSG + PAYLOAD + CRC
//                 (proto::enc... trong protocol.h) rồi write() xuống cổng -> PC
//                 + đếm SEQ riêng, + bộ đếm AUDIO_CHUNK riêng (audioChunk)
//     NHẬN        đọc byte (có hạn mức mỗi vòng, không chặn) -> proto::Decoder tách
//                 khung -> đưa từng khung cho hàm xử lý của .ino qua poll(now, ...)
//
//  Vì sao "now" lại được truyền vào poll()? Cả vòng loop của .ino chỉ dùng MỘT mốc
//  thời gian (g_now) để tránh trộn nhiều nguồn millis() (xem chú thích trong
//  esp32_cup_filler.ino). Lớp này KHÔNG tự gọi millis().
//
//  Giao thức từng byte: docs/PROTOCOL.md  ·  bộ mã hoá/giải mã: protocol.h
//  Chân GPIO: config.h  ·  bản dùng cáp USB: esp32_cup_filler_usb.cpp
// ===========================================================================
#ifndef CUPFILLER_SERIAL_LINK_H
#define CUPFILLER_SERIAL_LINK_H

#include <Arduino.h>

#include "config.h"
#include "protocol.h"

// ---------------------------------------------------------------------------
//  Cổng dùng để nói chuyện với PC - chọn bằng UART_USE_USB_SERIAL (config.h)
// ---------------------------------------------------------------------------
static inline HardwareSerial &pcPort() {
#if UART_USE_USB_SERIAL
  return Serial;                      // cáp USB của board (chip USB-UART có sẵn)
#else
  return Serial2;                     // UART2: GPIO16 = RX2, GPIO17 = TX2
#endif
}

// ---------------------------------------------------------------------------
//  Lớp giao tiếp: mở cổng, gửi từng loại gói tin, nhận và tách khung
// ---------------------------------------------------------------------------
class SerialLink {
 public:
  // ---- MỞ CỔNG (gọi một lần trong setup()) ----
  void begin() {
    HardwareSerial &p = pcPort();
    p.setRxBufferSize(2048);          // khung AUDIO_CHUNK dài -> đệm lớn cho khỏi mất gói
    p.setTxBufferSize(2048);
#if UART_USE_USB_SERIAL
    p.begin(UART_BAUD);                              // cổng USB của board
#else
    p.begin(UART_BAUD, SERIAL_8N1, PIN_UART_RX, PIN_UART_TX);   // UART2 + chân RX/TX
#endif
  }

  // ---- TÌNH TRẠNG KÊNH ----
  bool everRx() const { return _lastRxMs != 0; }             // đã từng nhận gói nào chưa
  uint32_t lastRxMs() const { return _lastRxMs; }            // lúc nhận gói CUỐI (ms)
  size_t txFree() { return pcPort().availableForWrite(); }   // còn chỗ trong đệm gửi?

  // ---- NHẬN: đọc byte -> tách khung -> gọi onFrame(f) cho từng khung hoàn chỉnh ----
  // budget = số byte tối đa đọc mỗi vòng loop (UART_RX_BUDGET) để không chặn việc khác.
  template <typename F>
  void poll(uint32_t now, F onFrame, uint16_t budget = UART_RX_BUDGET) {
    while (budget-- > 0 && pcPort().available() > 0) {
      if (_dec.push((uint8_t)pcPort().read())) _lastRxMs = now;
      while (_dec.pop(_frame)) onFrame(_frame);   // xử lý ngay, không để tràn hàng đợi
    }
    while (_dec.pop(_frame)) onFrame(_frame);
  }

  // ---- GỬI: số thứ tự gói + ghi một khung đã đóng gói ra cổng ----
  uint8_t nextSeq() {
    _seq++;
    return _seq;
  }

  void writeFrame(size_t len) {
    if (len) pcPort().write(_tx, len);
  }

  // ======== CÁC GÓI TIN ESP32 -> PC (đúng theo docs/PROTOCOL.md) ========
  void hello(uint8_t fwMajor, uint8_t caps, const uint16_t *presets, uint8_t nPresets,
             uint16_t flowDlPerSec) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encHello(w, nextSeq(), fwMajor, caps, presets, nPresets, flowDlPerSec));
  }

  void status(uint8_t state, uint8_t flags, uint16_t pouredMl, uint16_t targetMl, uint16_t maxMl,
              uint32_t uptimeMs) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encStatus(w, nextSeq(), state, flags, pouredMl, targetMl, maxMl, uptimeMs));
  }

  void progress(uint8_t state, uint16_t pouredMl, uint16_t targetMl) {
    uint16_t pct = targetMl ? (uint16_t)((100UL * pouredMl) / targetMl) : 0;
    if (pct > 200) pct = 200;                     // PC chỉ nhận 0..200%
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encFillProgress(w, nextSeq(), pouredMl, (uint8_t)pct, targetMl, state));
  }

  void cupPlaced(uint16_t distMm, uint16_t baseMm, uint16_t heightMm, uint8_t flags,
                 uint32_t uptimeMs) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encCupPlaced(w, nextSeq(), distMm, baseMm, heightMm, flags, uptimeMs));
  }

  void cupRemoved(uint8_t reason, uint32_t uptimeMs) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encCupRemoved(w, nextSeq(), reason, uptimeMs));
  }

  void presetSelected(uint8_t index, uint16_t ml, uint8_t source) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encPresetSelected(w, nextSeq(), index, ml, source));
  }

  void fillStarted(uint16_t ml, uint8_t mode, uint8_t source, uint16_t maxMl) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encFillStarted(w, nextSeq(), ml, mode, source, maxMl));
  }

  void fillDone(uint16_t pouredMl, uint16_t targetMl, uint8_t status, uint32_t elapsedMs) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encFillDone(w, nextSeq(), pouredMl, targetMl, status, elapsedMs));
  }

  void buttonEvent(uint8_t index, uint8_t event, uint16_t pressMs, uint8_t state) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encButtonEvent(w, nextSeq(), index, event, pressMs, state));
  }

  void voiceEvent(uint8_t kind, uint8_t index, uint8_t conf, uint8_t peaks, uint16_t rms,
                  uint16_t durMs) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encVoiceEvent(w, nextSeq(), kind, index, conf, peaks, rms, durMs));
  }

  void error(uint8_t code, const char *text) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encError(w, nextSeq(), code, text));
  }

  void ack(uint8_t ackedMsg, uint8_t status) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encAck(w, nextSeq(), ackedMsg, status));
  }

  void pong(uint32_t uptimeMs, uint8_t state, uint8_t flags) {
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encPong(w, nextSeq(), uptimeMs, state, flags));
  }

  // LOG chữ cho người dùng: hiện trên Serial Monitor (nếu DEBUG_SERIAL=1) và gửi
  // nguyên văn tiếng Việt lên PC để phần mềm hiện trong nhật ký.
  void log(const char *text) {
#if DEBUG_SERIAL
    Serial.println(text);
#endif
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encLog(w, nextSeq(), text));
  }

  // PCM của mic -> chuỗi AUDIO_CHUNK (chỉ gửi khi VOICE_MODE == 2).
  // AU_START = bắt đầu một đoạn nói -> đếm lại từ 0 (PC ghép các chunk theo seq8).
  void audioChunk(const int16_t *samples, uint16_t n, uint8_t flags) {
#if VOICE_MODE == 2
    if (flags & proto::AU_START) _audioSeq = 0;
    proto::Writer w(_tx, sizeof(_tx));
    writeFrame(proto::encAudioChunk(w, nextSeq(), _audioSeq++, flags, samples, n));
#else
    (void)samples;
    (void)n;
    (void)flags;
#endif
  }

 private:
  proto::Decoder _dec;                // ghép byte thành khung
  proto::Frame _frame;                // khung vừa tách được
  uint8_t _tx[320];                   // đệm dựng khung gửi (AUDIO_CHUNK 96 mẫu = 216 byte)
  uint8_t _seq = 0;                   // số thứ tự gói ESP -> PC
  uint8_t _audioSeq = 0;              // số thứ tự trong MỘT đoạn nói (AUDIO_CHUNK)
  uint32_t _lastRxMs = 0;             // 0 = chưa từng nhận được gói nào từ PC
};

#endif  // CUPFILLER_SERIAL_LINK_H
