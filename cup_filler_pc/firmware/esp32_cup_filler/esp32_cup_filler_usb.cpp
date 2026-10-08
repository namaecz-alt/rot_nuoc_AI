// ===========================================================================
//  esp32_cup_filler_usb.cpp  -  BẢN NÓI CHUYỆN VỚI PC QUA CÁP USB CỦA BOARD
//                               (KHÔNG cần mạch chuyển đổi USB-TTL)
//
//  Đây là file MỚI thêm, dùng để chọn kênh liên lạc với máy tính:
//     * esp32_cup_filler.ino       (file CŨ - GIỮ NGUYÊN)  -> UART2 GPIO16/17 + mạch USB-TTL
//     * esp32_cup_filler_usb.cpp   (file NÀY)              -> Serial.write() trên cổng USB
//                                                             có sẵn của ESP32 DevKit V1
//
//  CÁCH DÙNG (VS Code + PlatformIO):
//    1. Ở thanh trạng thái dưới cùng, chọn môi trường  esp32dev_usb
//    2. Bấm ✓ (Build) rồi → (Upload). Phía PC không đổi gì:
//         python3 tools/web.py --port COM5      (xem cổng trong Device Manager)
//         python3 tools/run_pc.py --port COM5
//
//  Toàn bộ logic nằm ở file cũ: file này chỉ bật 2 macro cấu hình rồi #include bản cũ,
//  nên sửa code chỉ sửa MỘT chỗ (esp32_cup_filler.ino + các .h) và hai bản luôn giống nhau.
//
//  Ghi chú kỹ thuật (vì sao có #if ở dưới):
//    * Env esp32dev_usb truyền -DCUP_USB_LINK=1 và build_src_filter loại file
//      esp32_cup_filler.ino.cpp (bản UART2 do PlatformIO sinh ra) -> chỉ biên dịch file này.
//    * Với env esp32dev, Arduino IDE, hay bộ test host_test: CUP_USB_LINK không được định
//      nghĩa -> file này RỖNG, không sinh setup()/loop() thứ hai, mọi thứ giữ nguyên.
//  Nội dung gói tin, mã giao thức... KHÔNG đổi: chỉ đổi cổng nào được dùng để gửi/nhận.
//  Xem docs/PROTOCOL.md và firmware/README.md (§3 "Nạp bằng VS Code + PlatformIO").
// ===========================================================================

#ifndef CUP_USB_LINK
#define CUP_USB_LINK 0
#endif

#if CUP_USB_LINK

// Nói chuyện với PC ngay trên cổng USB của board: HELLO/CUP_OK/AUDIO... đi bằng Serial.write().
// Chỉ có MỘT cổng USB nên PHẢI tắt log (config.h sẽ #error nếu quên).
#define UART_USE_USB_SERIAL 1
#define DEBUG_SERIAL 0

// Bản cũ (giữ nguyên nội dung) - include vào đây để hai bản dùng chung một mã nguồn.
#include "esp32_cup_filler.ino"

#endif  // CUP_USB_LINK
