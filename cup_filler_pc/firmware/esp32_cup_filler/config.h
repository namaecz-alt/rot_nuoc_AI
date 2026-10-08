// ===========================================================================
//  config.h - CẤU HÌNH PHẦN CỨNG ESP32 CHO MÁY RÓT NƯỚC
//
//  >>> CHỈNH FILE NÀY THEO MẠCH THẬT CỦA BẠN RỒI NẠP LẠI <<<
//
//  Quy ước đã chốt với phần mềm PC (đừng đổi nếu không sửa cả hai bên):
//    * Nút bấm : TÍCH CỰC MỨC THẤP (nhấn = LOW), chân đọc để INPUT_PULLUP
//    * Relay   : TÍCH CỰC MỨC CAO  (bật bơm = mức HIGH)
//    * Cốc đặt vào -> ESP gửi CUP_PLACED; PC nhận diện xong gửi CUP_OK thì
//      nút/mic mới được mở khoá; nút/mic chọn mức -> ESP đóng relay bật bơm.
// ===========================================================================
#ifndef CUPFILLER_CONFIG_H
#define CUPFILLER_CONFIG_H

// ---------------------------------------------------------------------------
//  Phiên bản & cổng UART nối với máy tính
// ---------------------------------------------------------------------------
#define FW_VERSION_MAJOR   1
#define FW_VERSION_MINOR   0
#define FW_NAME            "cup_filler_esp32"

#define UART_BAUD          921600     // 921600 để đủ chỗ truyền audio PCM lên PC
#define PIN_UART_RX        16         // ESP32 RX2  <- TX của mạch USB-UART/PC
#define PIN_UART_TX        17         // ESP32 TX2  -> RX của mạch USB-UART/PC
#define UART_RX_BUDGET     512        // số byte tối đa đọc mỗi vòng loop()
#define DEBUG_SERIAL       1          // 1 = in log ra cổng USB (Serial) để xem trên Serial Monitor

// ---------------------------------------------------------------------------
//  Nút bấm chọn mức nước  (TÍCH CỰC MỨC THẤP - INPUT_PULLUP)
// ---------------------------------------------------------------------------
#define N_BUTTONS          5
#define BUTTON_STARTS_POUR true       // nhấn NGẮN = chọn mức + bật bơm luôn
#define BUTTON_DEBOUNCE_MS 25         // chống dội
#define BUTTON_LONG_MS     1500       // >= mức này = NHẤN GIỮ (chỉ chọn mức, chưa bơm)
#define BUTTON_ESTOP_MS    2000       // >= mức này = DỪNG KHẨN CẤP (ngắt bơm ngay)

// Chân cho từng nút (theo thứ tự preset: 100/150/200/250/300 ml)
static const uint8_t BUTTON_PINS[N_BUTTONS] = {32, 33, 25, 26, 27};
// Mức nước tương ứng (ml) - phải khớp control.presets_ml trong settings.yaml
static const uint16_t PRESET_ML[N_BUTTONS] = {100, 150, 200, 250, 300};
#define PRESET_INDEX_DEFAULT 2        // 200 ml

// ---------------------------------------------------------------------------
//  Relay điều khiển bơm  (TÍCH CỰC MỨC CAO)
// ---------------------------------------------------------------------------
#define PIN_RELAY          23
#define RELAY_ACTIVE_HIGH  1          // 1 = mức HIGH là BẬT bơm (theo yêu cầu)
#define RELAY_USE_PWM      0          // 1 = băm xung nhanh (MOSFET), 0 = relay cơ
#define RELAY_PWM_HZ       200        // chỉ dùng khi RELAY_USE_PWM = 1

#define FLOW_ML_PER_S      40.0f      // lưu lượng đã HIỆU CHUẨN (ml/s) khi bơm 100%
#define FLOW_MIN_DUTY_PCT  8          // duty nhỏ nhất còn bơm được (relay đóng/mở chậm)
#define DUTY_CYCLE_MS      2000       // chu kỳ băm relay khi duty < 100%
#define IN_FLIGHT_ML       4.0f       // nước còn trên đường ống khi ngắt bơm
#define MAX_FILL_ML        500        // TRẦN TUYỆT ĐỐI - ESP tự ngắt dù PC có lỗi
#define POUR_TIMEOUT_MS    60000      // quá lâu -> tự ngắt
#define FINISH_ZERO_DUTY_MS 3000      // (chế độ PC điều khiển) duty=0 đủ lâu -> kết thúc
#define FORCE_OFF_ON_BOOT  1          // 1 = kéo relay về OFF ngay khi khởi động

// ---------------------------------------------------------------------------
//  Cảm biến phát hiện CỐC ĐẶT VÀO
//    0 = siêu âm HC-SR04 (mặc định)    1 = cảm biến hồng ngoại (digital, LOW)
//    2 = công tắc hành trình (digital, LOW)
// ---------------------------------------------------------------------------
#define CUP_SENSOR_TYPE    0
#define PIN_TRIG           5          // HC-SR04
#define PIN_ECHO           18
#define PIN_CUP_DIGITAL    34         // dùng cho loại 1 / 2 (chỉ đọc - input only)
#define CUP_BASELINE_MM    150.0f     // khoảng cách khi KHAY TRỐNG (đo lúc khởi động)
#define CUP_MIN_HEIGHT_MM  30.0f      // thấp hơn mức này coi như không có cốc
#define CUP_MAX_DISTANCE_MM 400.0f    // xa hơn mức này -> bỏ qua (không có gì)
#define CUP_DEBOUNCE_MS    300        // phải ổn định bấy lâu mới báo "đã đặt cốc"
#define CUP_MISSING_MS     250        // mất cốc bấy lâu -> báo "đã nhấc cốc"
#define CUP_SAMPLE_MS      60         // chu kỳ lấy mẫu cảm biến
#define CUP_MEDIAN_N       3          // lọc trung vị N mẫu

// ---------------------------------------------------------------------------
//  Mic ghi âm để chọn mức nước bằng giọng nói
//    0 = KHÔNG có mic    1 = mic ANALOG (MAX9814/MAX4466 -> ADC)  [mặc định]
//    2 = mic I2S số (INMP441/ICS-43434)
// ---------------------------------------------------------------------------
#define MIC_TYPE           1
#define MIC_SAMPLE_RATE    16000
#define MIC_CHUNK_SAMPLES  96         // số mẫu mỗi gói AUDIO_CHUNK gửi lên PC
#define PIN_MIC_ADC        35         // mic analog (chỉ đọc)
#define PIN_I2S_SCK        14         // INMP441: SCK
#define PIN_I2S_WS         15         // INMP441: WS
#define PIN_I2S_SD         33         // INMP441: SD (dữ liệu ra)
#define MIC_GAIN_NUM       3          // khuếch đại số (tử)
#define MIC_GAIN_DEN       1          // khuếch đại số (mẫu)  -> 3/1 = x3
#define VAD_ON_RMS         260        // RMS (thang 0..32767) để coi là có tiếng
#define VAD_OFF_RMS        150        // dưới mức này coi là im lặng
#define VAD_START_FRAMES   4          // số khung 10 ms liên tiếp vượt ngưỡng để BẮT ĐẦU
#define VAD_END_MS         350        // im lặng bấy lâu -> KẾT THÚC đoạn nói
#define VAD_MAX_SEGMENT_MS 3000       // đoạn nói dài tối đa
#define VOICE_MIN_PEAKS    1          // số "tiếng" tối thiểu để coi là có nói

// Chế độ nhận dạng giọng nói:
//   1 = ESP tự đếm số tiếng rồi chọn mức + bơm (không cần PC, hoạt động cả khi PC bận)
//   2 = ESP gửi PCM lên PC, PC nhận dạng rồi gửi lệnh xuống (mặc định)
#define VOICE_MODE         2
#define VOICE_AUTO_START   true       // nhận ra mức thì bơm luôn (chỉ áp dụng VOICE_MODE=1)

// ---------------------------------------------------------------------------
//  Liên lạc & an toàn
// ---------------------------------------------------------------------------
#define MODE_PC_CONTROLS_PUMP_BIT 0   // bit 0 của SET_MODE: PC điều khiển bơm
#define MODE_VOICE_STREAM_BIT     1   // bit 1: gửi PCM lên PC
#define STATUS_PERIOD_MS   500        // gửi STATUS định kỳ
#define PROGRESS_PERIOD_MS 200        // gửi FILL_PROGRESS định kỳ khi đang bơm
#define HELLO_PERIOD_MS    2000       // nhắc lại HELLO khi chưa được PC trả lời
#define CUP_RETRY_MS       2000       // nhắc lại CUP_PLACED khi PC chưa gửi CUP_OK
#define WAIT_PC_TIMEOUT_MS 15000      // quá lâu không thấy CUP_OK -> báo lỗi
#define LINK_TIMEOUT_MS    3000       // không nhận gói nào từ PC -> coi như mất liên lạc
#define LINK_STOPS_PUMP    1          // mất liên lạc -> NGẮT BƠM (an toàn)
#define ALLOW_MANUAL_WHEN_LINK_LOST 0 // 1 = sau khi mất liên lạc vẫn cho bấm nút dự phòng

// LED báo trạng thái (GPIO2 = LED onboard trên board devkit)
#define PIN_LED            2
#define USE_STATUS_LED     1

#endif  // CUPFILLER_CONFIG_H
