// ===========================================================================
//  pi_link.h - ESP32 <-> MÁY TÍNH/Pi, 961000 baud
//
//  * Mode AUTO: Serial (USB board) và Serial2
//    (RX=GPIO16, TX=GPIO17) cùng nghe; cổng gửi khung hợp lệ đầu tiên sẽ
//    thành cổng host đang hoạt động. Trước khi chọn được cổng, HELLO/
//    INPUT_STATE được gửi ra cả hai để máy tính cắm cách nào cũng thấy ESP.
//  * Mode UART2: chỉ Serial2 mang dữ liệu, USB còn riêng cho debug 115200.
//  * Mode USB: chỉ Serial của board mang dữ liệu; debug chữ tự tắt.
//  * Task audio và loop() đều có thể gửi frame; mọi lần ghi UART đi qua mutex
//    nên hai frame không bao giờ xen byte vào nhau.
//  * Nhận: đọc không chặn theo ngân sách byte, Decoder cũ tự tìm lại AA55 sau
//    rác/CRC hỏng.
// ===========================================================================
#ifndef CUPFILLER_PI_LINK_H
#define CUPFILLER_PI_LINK_H

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "pi_config.h"
#include "pi_protocol.h"

class PiLink {
 public:
  void begin() {
    _txMutex = xSemaphoreCreateMutex();
#if PI_LINK_USE_UART2
    Serial2.setRxBufferSize(PI_UART_RX_BUFFER);
    Serial2.setTxBufferSize(PI_UART_TX_BUFFER);
    Serial2.begin(PI_UART_BAUD, SERIAL_8N1, PIN_PI_UART_RX, PIN_PI_UART_TX);
#endif
#if PI_LINK_USE_USB
    Serial.setRxBufferSize(PI_UART_RX_BUFFER);
    Serial.setTxBufferSize(PI_UART_TX_BUFFER);
    Serial.begin(PI_UART_BAUD, SERIAL_8N1);
#endif
  }

  bool everRx() const { return _lastRxMs != 0; }
  uint32_t lastRxMs() const { return _lastRxMs; }
  uint8_t activeTransport() const { return _activePort; }
  size_t txFree() {
#if PI_LINK_TRANSPORT == PI_LINK_TRANSPORT_AUTO
    if (_activePort == PORT_UART2) return (size_t)Serial2.availableForWrite();
    if (_activePort == PORT_USB) return (size_t)Serial.availableForWrite();
    size_t uartFree = (size_t)Serial2.availableForWrite();
    size_t usbFree = (size_t)Serial.availableForWrite();
    return uartFree < usbFree ? uartFree : usbFree;
#elif PI_LINK_USE_UART2
    return (size_t)Serial2.availableForWrite();
#else
    return (size_t)Serial.availableForWrite();
#endif
  }

  template <typename F>
  void poll(uint32_t nowMs, F onFrame, uint32_t budget = PI_UART_POLL_BUDGET) {
#if PI_LINK_TRANSPORT == PI_LINK_TRANSPORT_AUTO
    // Host cũ im lặng quá hạn liên lạc: mở khóa để cổng còn lại có thể
    // nhận HELLO/INPUT_STATE và trở thành host mới.
    if (_activePort != PORT_NONE && _lastRxMs != 0 &&
        (nowMs - _lastRxMs) >= LINK_TIMEOUT_MS) {
      _activePort = PORT_NONE;
    }
#else
    (void)nowMs;
#endif

#if PI_LINK_USE_UART2
    {
      uint32_t portBudget = budget;
      while (portBudget-- > 0 && Serial2.available() > 0) {
        if (_decUart2.push((uint8_t)Serial2.read())) {
          _lastRxMs = nowMs;
          _activePort = PORT_UART2;
        }
        while (_decUart2.pop(_frame)) onFrame(_frame);
      }
      while (_decUart2.pop(_frame)) onFrame(_frame);
    }
#endif

#if PI_LINK_USE_USB
    {
      uint32_t portBudget = budget;
      while (portBudget-- > 0 && Serial.available() > 0) {
        if (_decUsb.push((uint8_t)Serial.read())) {
          _lastRxMs = nowMs;
          _activePort = PORT_USB;
        }
        while (_decUsb.pop(_frame)) onFrame(_frame);
      }
      while (_decUsb.pop(_frame)) onFrame(_frame);
    }
#endif
  }

  // ---------------------------------------------------- ESP32 -> Pi: chung --
  void hello(const uint16_t *presetsMl, uint8_t nPresets, uint16_t flowX10) {
    uint8_t tx[96];
    proto::Writer w(tx, sizeof(tx));
    const uint8_t caps = proto::ESP_CAP_BUTTONS | proto::ESP_CAP_VOICE |
                         proto::ESP_CAP_FLOW_CAL;
    writeFrame(tx, proto::encHello(w, nextSeq(), PI_FW_VERSION_MAJOR, caps,
                                   presetsMl, nPresets, flowX10));
  }

  void presetSelected(uint8_t index, uint16_t ml, uint8_t source) {
    uint8_t tx[48];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encPresetSelected(w, nextSeq(), index, ml, source));
  }

  void status(uint8_t state, uint8_t flags, uint16_t pouredMl,
              uint16_t targetMl, uint16_t maxMl, uint32_t uptimeMs) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encStatus(w, nextSeq(), state, flags, pouredMl,
                                    targetMl, maxMl, uptimeMs));
  }

  void progress(uint8_t state, uint16_t pouredMl, uint16_t targetMl) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    uint16_t pct = targetMl ? (uint16_t)((100UL * pouredMl) / targetMl) : 0;
    if (pct > 200) pct = 200;
    writeFrame(tx, proto::encFillProgress(w, nextSeq(), pouredMl, (uint8_t)pct,
                                          targetMl, state));
  }

  void cupPlaced(uint32_t uptimeMs) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    // Cảm biến số chỉ cho biết có/không: distance/base/height để 0, flags=1.
    writeFrame(tx, proto::encCupPlaced(w, nextSeq(), 0, 0, 0, 0x01, uptimeMs));
  }

  void cupRemoved(uint8_t reason, uint32_t uptimeMs) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encCupRemoved(w, nextSeq(), reason, uptimeMs));
  }

  void fillStarted(uint16_t targetMl, uint8_t mode, uint8_t source,
                   uint16_t maxMl) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encFillStarted(w, nextSeq(), targetMl, mode, source,
                                         maxMl));
  }

  void fillDone(uint16_t pouredMl, uint16_t targetMl, uint8_t status,
                uint32_t elapsedMs) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encFillDone(w, nextSeq(), pouredMl, targetMl, status,
                                      elapsedMs));
  }

  void buttonEvent(uint8_t index, uint8_t event, uint16_t pressMs,
                   uint8_t state) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encButtonEvent(w, nextSeq(), index, event, pressMs,
                                         state));
  }

  void error(uint8_t code, const char *text) {
    uint8_t tx[128];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encError(w, nextSeq(), code, text));
  }

  void ack(uint8_t ackedMsg, uint8_t status) {
    uint8_t tx[32];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encAck(w, nextSeq(), ackedMsg, status));
  }

  void pong(uint32_t uptimeMs, uint8_t state, uint8_t flags) {
    uint8_t tx[64];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encPong(w, nextSeq(), uptimeMs, state, flags));
  }

  void log(const char *text) {
#if DEBUG_SERIAL
    Serial.println(text);
#endif
    uint8_t tx[256];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encLog(w, nextSeq(), text));
  }

  // ------------------------------------------------ ESP32 -> Pi: bản Pi mới --
  // Payload: [cảm biến, START, mức1, mức2, mức3], từng byte 0x00/0x01.
  void inputState(bool sensor, bool start,
                  const bool levels[piproto::N_LEVELS]) {
    uint8_t tx[32];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, piproto::encInputState(w, nextSeq(), sensor, start, levels));
  }

  void levelSelected(uint8_t index, uint8_t percent, uint8_t source) {
    uint8_t tx[40];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, piproto::encLevelSelected(w, nextSeq(), index, percent,
                                             source));
  }

  void startRequest(uint8_t selectedIndex, uint8_t percent) {
    uint8_t tx[40];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, piproto::encStartRequest(w, nextSeq(), selectedIndex,
                                            percent));
  }

  // Mic PCM (gọi từ audio task): dùng AUDIO_CHUNK của giao thức cũ.
  void audioChunk(const int16_t *samples, uint16_t n, uint8_t flags) {
    if (flags & proto::AU_START) _audioSeq = 0;
    uint8_t tx[320];
    proto::Writer w(tx, sizeof(tx));
    writeFrame(tx, proto::encAudioChunk(w, nextSeq(), _audioSeq++, flags,
                                        samples, n));
  }

 private:
  uint8_t nextSeq() {
    if (_txMutex != NULL) xSemaphoreTake(_txMutex, portMAX_DELAY);
    uint8_t s = ++_seq;
    if (_txMutex != NULL) xSemaphoreGive(_txMutex);
    return s;
  }

  void writeFrame(const uint8_t *buf, size_t len) {
    if (buf == NULL || len == 0) return;
    if (_txMutex != NULL) xSemaphoreTake(_txMutex, portMAX_DELAY);
    const uint8_t active = _activePort;
#if PI_LINK_USE_UART2
    if (active == PORT_NONE || active == PORT_UART2) {
      Serial2.write(buf, len);
    }
#endif
#if PI_LINK_USE_USB
    if (active == PORT_NONE || active == PORT_USB) {
      Serial.write(buf, len);
    }
#endif
    if (_txMutex != NULL) xSemaphoreGive(_txMutex);
  }

  enum PortId : uint8_t {
    PORT_NONE = 0,
    PORT_UART2 = PI_LINK_TRANSPORT_UART2,
    PORT_USB = PI_LINK_TRANSPORT_USB_SERIAL,
  };

  SemaphoreHandle_t _txMutex = NULL;
#if PI_LINK_USE_UART2
  proto::Decoder _decUart2;
#endif
#if PI_LINK_USE_USB
  proto::Decoder _decUsb;
#endif
  proto::Frame _frame;
#if PI_LINK_TRANSPORT == PI_LINK_TRANSPORT_UART2
  volatile uint8_t _activePort = PORT_UART2;
#elif PI_LINK_TRANSPORT == PI_LINK_TRANSPORT_USB_SERIAL
  volatile uint8_t _activePort = PORT_USB;
#else
  volatile uint8_t _activePort = PORT_NONE;
#endif
  uint8_t _seq = 0;
  uint8_t _audioSeq = 0;
  uint32_t _lastRxMs = 0;
};

#endif  // CUPFILLER_PI_LINK_H
