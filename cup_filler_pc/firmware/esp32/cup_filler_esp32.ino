/* =====================================================================
   MAY ROT NUOC TU DONG - FIRMWARE ESP32
   ---------------------------------------------------------------------
   Vai tro cua ESP32 trong he thong:
     1. Doc CAM BIEN COC. Khi co coc -> gui goi tin $CUP,1 qua UART len
        may tinh. May tinh LUC DO MOI bat camera va nhan dien coc.
     2. Doi may tinh xac nhan $ACK,1 (da thay dung coc trong anh).
        Chi khi do ESP32 moi cho phep nguoi dung tac dong.
     3. Nhan NUT CHON MUC (5 nut, tich cuc THAP, INPUT_PULLUP) hoac
        lenh GIONG NOI tu mic -> gui $START,<ml>.
     4. Nhan $PUMP,<duty> tu may tinh -> dieu khien RELAY bom
        (tich cuc CAO). May tinh van quyet dinh luc nao bom, bom bao lau.
     5. An toan: nha coc / mat lien lac voi may tinh -> cat relay ngay.

   NOI DAY (xem chi tiet trong README_ESP32.md):
     GPIO23 -> IN relay bom        (tich cuc CAO)
     GPIO 2 -> LED trang thai
     GPIO 4 -> coid beep (tuy chon)
     GPIO32 <- cam bien coc        (INPUT_PULLUP, co coc = 0)
     GPIO25/26/27/14/13 <- nut muc 100/150/200/250/300 ml (INPUT_PULLUP)
     GPIO33 <- nut DUNG            (INPUT_PULLUP)
     USB    <-> may tinh, UART 115200 8N1

   Nap: Arduino IDE (chon board "ESP32 Dev Module") hoac `pio run -t upload`.
   ===================================================================== */
#include <Arduino.h>

#include "config.h"
#include "device_fsm.h"
#include "uart_protocol.h"
#include "voice_input.h"

using namespace cupfiller;

static DeviceFSM fsm;

void setup() {
  // UART voi may tinh. CHU Y: luc ESP32 khoi dong, ROM in mot it rac o
  // 115200 baud - bo giai ma phia may tinh tu bo qua (khong dung khuung $..*).
  Serial.begin(LINK_BAUD);
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  Serial.setDebugOutput(false);
#endif
  const unsigned long t0 = millis();
  while (!Serial && (millis() - t0) < 1500) {
    delay(5);  // doi cong USB CDC san sang (khong bat buoc voi UART bridge)
  }

  fsm.begin();  // tat relay, dat INPUT_PULLUP, gui $BOOT
}

void loop() {
  fsm.loop();
  delay(2);  // nhuong CPU; du nhanh cho loc rung 25 ms va soft-PWM 400 ms
}
