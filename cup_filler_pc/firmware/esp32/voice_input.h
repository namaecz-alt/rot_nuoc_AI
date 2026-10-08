// =====================================================================
//  DAU VAO GIONG NOI (MIC) - tra ve cung mot bo ma lenh voi nut bam
//  ---------------------------------------------------------------------
//  VOICE_MODE (config.h):
//    0 : khong dung mic
//    1 : module giong noi xuat XUNG ra GPIO (SU-03T che do GPIO, mach
//        "moi cau lenh mot chan"). Chan doc INPUT_PULLUP, tich cuc THAP
//        -> nhat quan voi nut bam. Day la che do MAC DINH va da duoc
//        chay thu trong bo test (khong can module that).
//    2 : module giong noi truyen UART (SU-03T/LD3320 che do UART).
//        Moi cau lenh module gui mot ma ngan, vi du "A1\r\n".
//    3 : ESP-SR tren ESP32-S3 + mic I2S INMP441 (xem voice_input_espsr.cpp,
//        chi bien dich khi #define USE_ESP_SR).
//
//  Du ban dung micro nao, phan con lai cua firmware KHONG doi: tat ca
//  quy ve mot ma lenh VoiceCmd -> chon muc / bat dau / dung.
// =====================================================================
#ifndef CUPFILLER_VOICE_INPUT_H
#define CUPFILLER_VOICE_INPUT_H

#include <stdint.h>

namespace cupfiller {

enum VoiceCmd {
  VOICE_NONE = -1,
  VOICE_LEVEL_1 = 0,   // "mot tram"   / "muc mot"
  VOICE_LEVEL_2 = 1,   // "mot nam muoi"
  VOICE_LEVEL_3 = 2,   // "hai tram"
  VOICE_LEVEL_4 = 3,   // "hai nam muoi"
  VOICE_LEVEL_5 = 4,   // "ba tram"
  VOICE_START = 5,     // "rot nuoc" / "bat dau"
  VOICE_STOP = 6       // "dung lai"
};

void voice_begin();

// Tra ve VoiceCmd khi co lenh moi, nguoc lai VOICE_NONE.
int voice_poll(uint32_t now_ms);

// Ten che do dang dung (de gui log len may tinh).
const char *voice_backend_name();

}  // namespace cupfiller

#endif  // CUPFILLER_VOICE_INPUT_H
