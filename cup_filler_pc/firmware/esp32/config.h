// =====================================================================
//  CAU HINH PHAN CUNG + THAM SO HOAT DONG CUA ESP32
//  ---------------------------------------------------------------------
//  QUY UOC DA CHOT (dung theo yeu cau de bai):
//    * Cac NUT BAM: tich cuc muc THAP (nhan = 0), chan dat INPUT_PULLUP.
//      Mot dau nut noi vao GPIO, dau kia noi GND. KHONG can tro ngoai.
//    * CAM BIEN COC: ngo ra so, tich cuc muc THAP (co coc = 0),
//      cung dung INPUT_PULLUP. Doi duoc bang CUP_ACTIVE_LEVEL.
//    * RELAY BOM: tich cuc muc CAO (IN = 1 -> relay hut -> bom chay).
//      Luc khoi dong chan duoc keo THAP va relay duoc tat ngay de bom
//      khong chay luc ESP32 dang nap firmware.
//    * UART voi may tinh: 115200 8N1, dung cong Serial (USB) cua ESP32.
//
//  BAN DO CHAN mac dinh (ESP32 DevKit V1 / NodeMCU-32S):
//
//    GPIO23  -> IN relay bom        (tich cuc CAO)
//    GPIO 2  -> LED trang thai      (LED co san tren mach)
//    GPIO 4  -> coid beep (tuy chon, tich cuc CAO; -1 = khong dung)
//    GPIO32  <- cam bien co coc     (INPUT_PULLUP, co coc = 0)
//    GPIO25  <- nut muc 1 (100 ml)  (INPUT_PULLUP)
//    GPIO26  <- nut muc 2 (150 ml)
//    GPIO27  <- nut muc 3 (200 ml)
//    GPIO14  <- nut muc 4 (250 ml)
//    GPIO13  <- nut muc 5 (300 ml)
//    GPIO33  <- nut DUNG / huy      (INPUT_PULLUP)
//    GPIO16/17 -> UART2 voi module giong noi (SU-03T / LD3320), tuy chon
//
//  LUU Y: tranh GPIO 6..11 (noi flash), GPIO 12 (strapping - keo len luc
//  khoi dong se loi nap), GPIO 34..39 (khong co pull-up noi).
// =====================================================================
#ifndef CUPFILLER_CONFIG_H
#define CUPFILLER_CONFIG_H

#include <stdint.h>

// ---- UART voi may tinh ------------------------------------------------
#ifndef LINK_BAUD
#define LINK_BAUD 115200
#endif

// ---- Relay bom --------------------------------------------------------
#ifndef PIN_RELAY_PUMP
#define PIN_RELAY_PUMP 23
#endif
#ifndef RELAY_ACTIVE_HIGH
#define RELAY_ACTIVE_HIGH 1   // 1 = muc CAO la bom chay (yeu cau de bai)
#endif
// Relay khong bam nhanh nhu MOSFET -> bam mem (soft-PWM) chu ky dai.
// Neu dung MOSFET/SSR, giam xuong 20 ms de dieu khien min hon.
#ifndef SOFTPWM_PERIOD_MS
#define SOFTPWM_PERIOD_MS 400
#endif

// ---- LED / coid -------------------------------------------------------
#ifndef PIN_LED
#define PIN_LED 2
#endif
#ifndef PIN_BUZZER
#define PIN_BUZZER 4   // -1 = khong dung
#endif
#ifndef BUZZER_ACTIVE_HIGH
#define BUZZER_ACTIVE_HIGH 1
#endif

// ---- Cam bien coc -----------------------------------------------------
#ifndef PIN_CUP_SENSOR
#define PIN_CUP_SENSOR 32
#endif
#ifndef CUP_ACTIVE_LEVEL
#define CUP_ACTIVE_LEVEL 0   // 0 = co coc thi chan xuong THAP (IR/cam ung pho bien)
#endif
// Coc phai nam yen tren cam bien bao lau thi moi bao cho may tinh (chong rung).
#ifndef CUP_DEBOUNCE_MS
#define CUP_DEBOUNCE_MS 150
#endif

// ---- Nut bam (tich cuc THAP, INPUT_PULLUP) ---------------------------
#define N_LEVEL_BUTTONS 5
static const uint8_t kLevelPins[N_LEVEL_BUTTONS] = {25, 26, 27, 14, 13};
static const int kDefaultPresets[N_LEVEL_BUTTONS] = {100, 150, 200, 250, 300};

#ifndef PIN_BTN_STOP
#define PIN_BTN_STOP 33
#endif
#ifndef BTN_ACTIVE_LEVEL
#define BTN_ACTIVE_LEVEL 0
#endif
#ifndef BTN_DEBOUNCE_MS
#define BTN_DEBOUNCE_MS 25
#endif
// Giu nut muc lau hon so nay = CHI CHON MUC, khong bat dau rot.
#ifndef BTN_LONG_PRESS_MS
#define BTN_LONG_PRESS_MS 900
#endif

// ---- Giong noi (mic) --------------------------------------------------
// 0 = khong dung
// 1 = module giong noi xuat XUNG ra tung chan (SU-03T che do GPIO) - mac dinh
// 2 = module giong noi truyen UART (SU-03T/LD3320 che do UART)
// 3 = ESP-SR tren ESP32-S3 + mic I2S INMP441 (can #define USE_ESP_SR, xem README)
#ifndef VOICE_MODE
#define VOICE_MODE 1
#endif
#ifndef VOICE_UART_NUM
#define VOICE_UART_NUM 2
#endif
#ifndef PIN_VOICE_RX
#define PIN_VOICE_RX 16
#endif
#ifndef PIN_VOICE_TX
#define PIN_VOICE_TX 17
#endif
// Che do 1: moi lenh giong noi mot chan, tich cuc THAP + INPUT_PULLUP
#define N_VOICE_PINS 6
static const uint8_t kVoicePins[N_VOICE_PINS] = {5, 18, 19, 21, 22, 34};
// y nghia tung chan: 0..4 = chon muc 1..5, 5 = bat dau rot, 6 = dung
// GPIO34 khong co pull-up noi: ngo ra module giong noi la push-pull nen van
// doc duoc; neu module cua ban ngo ra de treo thi them tro keo len 10k ngoai.

// ---- An toan / thoi gian ---------------------------------------------
// Cho may tinh xac nhan "da thay coc" (ACK,1) sau khi bao CUP,1.
#ifndef ACK_TIMEOUT_MS
#define ACK_TIMEOUT_MS 6000
#endif
// Mat lien lac voi may tinh qua lau -> NGAT RELAY, huy trang thai cho phep rot.
#ifndef PC_TIMEOUT_MS
#define PC_TIMEOUT_MS 800
#endif
// Chu ky gui ban tin HB ve may tinh.
#ifndef HB_PERIOD_MS
#define HB_PERIOD_MS 250
#endif
// May tinh khong xac nhan duoc coc -> bao lai CUP,1 sau khoang thoi gian nay.
#ifndef CUP_RETRY_MS
#define CUP_RETRY_MS 2500
#endif
// Mot lan rot keo dai qua so nay -> tu cat bom (an toan, phong khi may tinh treo
// o trang thai rot ma khong gui STATE,DONE).
#ifndef FILL_MAX_MS
#define FILL_MAX_MS 60000
#endif
// LED trang thai sang khi chan o muc CAO (1) hay THAP (0).
#ifndef LED_ACTIVE_HIGH
#define LED_ACTIVE_HIGH 1
#endif
// Sau khi rot xong / bao loi, cho nguoi dung lay coc ra roi dat lai.
#ifndef COOLDOWN_MS
#define COOLDOWN_MS 400
#endif

#endif  // CUPFILLER_CONFIG_H
