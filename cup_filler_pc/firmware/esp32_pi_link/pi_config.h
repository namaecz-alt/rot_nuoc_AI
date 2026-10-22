// ===========================================================================
//  pi_config.h - CẤU HÌNH PHẦN CỨNG BẢN ESP32 <-> RASPBERRY PI
//
//  Board: ESP32 DevKit V1 (ESP32-WROOM-32, 30 chân)
//  Sơ đồ rev mới:
//    * cb (cảm biến cốc) ................ GPIO34
//    * button1 / button2 / button3 ...... GPIO32 / GPIO33 / GPIO25 (mức 30/60/90)
//    * button4 = START .................. GPIO26
//    * relay bơm ........................ GPIO27 (tích cực mức CAO)
//    * UART2 lên Raspberry Pi ........... RX2=GPIO16, TX2=GPIO17, 961000 baud
//    * LCD 2004 I2C ...................... SDA=GPIO21, SCL=GPIO22 (PCF8574)
//    * I2S dùng chung BCLK/LRCLK ........ GPIO18 / GPIO19
//    * INMP441 SD (mic -> ESP32) ........ GPIO23
//    * MAX98357A DIN (ESP32 -> loa) ..... GPIO13  <-- ẢNH CROP CHƯA HIỆN NET NÀY.
//         Nếu mạch của bạn nối DIN vào chân khác, CHỈ SỬA PIN_I2S_SPK_DIN.
//         DIN loa và SD mic KHÔNG được dùng chung một GPIO khi chạy song công.
//
//  Các nút đều TÍCH CỰC MỨC THẤP: nhấn = LOW, dùng INPUT_PULLUP.
//  Cảm biến ở GPIO34: chân input-only, KHÔNG có pull-up nội. Module cảm biến
//  phải tự xuất mức 0/3V3 hoặc mạch phải có điện trở kéo ngoài 10 kΩ.
// ===========================================================================
#ifndef CUPFILLER_PI_CONFIG_H
#define CUPFILLER_PI_CONFIG_H

#include <stdint.h>

// ---------------------------------------------------------------------------
//  Phiên bản / debug
// ---------------------------------------------------------------------------
#define PI_FW_NAME            "esp32_pi_link"
#define PI_FW_VERSION_MAJOR   2
#define PI_FW_VERSION_MINOR   0
#define DEBUG_SERIAL          1        // log USB Serial Monitor 115200; KHÔNG lẫn UART2
#define DEBUG_BAUD            115200

// ---------------------------------------------------------------------------
//  UART2 nối Raspberry Pi (header H1: 1=TX2, 2=RX2, 3=GND)
//  ESP32 TX2 -> Pi RXD, ESP32 RX2 <- Pi TXD, GND chung.
//
//  Theo yêu cầu dùng 961000 baud. Cả ESP32 và Pi phải mở ĐÚNG tốc độ này.
//  Nếu driver UART của Pi không nhận baud lẻ, đổi CẢ HAI BÊN thành 921600
//  (vẫn dư băng thông cho PCM 16 kHz hai chiều).
// ---------------------------------------------------------------------------
#define PI_UART_BAUD          961000
#define PIN_PI_UART_RX        16       // ESP32 RX2
#define PIN_PI_UART_TX        17       // ESP32 TX2
#define PI_UART_RX_BUFFER     8192
#define PI_UART_TX_BUFFER     8192
#define PI_UART_POLL_BUDGET   4096     // byte đọc tối đa mỗi vòng loop

// ---------------------------------------------------------------------------
//  Cảm biến cốc "cb" + 4 nút
// ---------------------------------------------------------------------------
#define PIN_CUP_SENSOR        34
// 1 = có cốc khi chân LOW (module hồng ngoại digital thường như vậy).
// Nếu Pi nhận byte cảm biến ngược, đổi giá trị này thành 0.
#define SENSOR_ACTIVE_LOW     1
#define SENSOR_DEBOUNCE_MS    80

#define N_LEVEL_BUTTONS       3
#define N_INPUTS              5        // sensor + START + 3 level
#define PIN_START_BUTTON      26
static constexpr uint8_t LEVEL_BUTTON_PINS[N_LEVEL_BUTTONS] = {32, 33, 25};
static constexpr uint8_t LEVEL_PERCENT[N_LEVEL_BUTTONS]     = {30, 60, 90};
#define DEFAULT_LEVEL_INDEX   1        // 60%

#define BUTTON_DEBOUNCE_MS    25
#define BUTTON_LONG_MS        1500
#define BUTTON_ESTOP_MS       2000     // giữ START/nút mức >=2 s -> ngắt bơm tại ESP
#define INPUT_STATE_PERIOD_MS 100      // ngoài gửi khi thay đổi còn gửi nhịp 10 Hz

// ---------------------------------------------------------------------------
//  Relay / bơm
// ---------------------------------------------------------------------------
#define PIN_RELAY             27
#define RELAY_ACTIVE_HIGH     1        // HIGH = bật bơm
#define FLOW_ML_PER_S         40.0f    // hiệu chuẩn thực tế của bơm
#define IN_FLIGHT_ML          4.0f
#define MAX_FILL_ML           500      // trần an toàn nếu Pi chưa gửi CUP_OK.max_ml
#define POUR_TIMEOUT_MS       60000
#define FINISH_ZERO_DUTY_MS   3000     // Pi giữ duty=0 đủ 3 s -> ESP kết thúc lượt
#define PUMP_DUTY_CYCLE_MS    2000     // băm chậm cho relay cơ khi duty <100%
#define PUMP_MIN_DUTY_PCT     8

// ---------------------------------------------------------------------------
//  Audio I2S: INMP441 mic + MAX98357A loa
// ---------------------------------------------------------------------------
#define AUDIO_SAMPLE_RATE     16000
#define AUDIO_CHUNK_SAMPLES   96       // 6 ms / gói, vừa trần payload giao thức cũ
#define AUDIO_BLOCK_FRAMES    160      // task xử lý theo khối 10 ms

#define PIN_I2S_BCLK          18
#define PIN_I2S_LRCLK         19
#define PIN_I2S_MIC_SD        23       // INMP441 SD/DOUT -> ESP32 data-in
#ifndef PIN_I2S_SPK_DIN
#define PIN_I2S_SPK_DIN       13       // ESP32 data-out -> MAX98357A DIN (XEM GHI CHÚ TRÊN)
#endif

#define MIC_CHANNEL_LEFT      1        // INMP441 L/R nối GND = kênh trái; nối 3V3 thì đặt 0
#define MIC_SHIFT             14       // INMP441 24-bit trong slot 32-bit -> int16 + gain
#define MIC_VAD_ON_RMS        500      // thang int16 0..32767; chỉnh theo phòng/PCB
#define MIC_VAD_OFF_RMS       250
#define MIC_VAD_START_BLOCKS  4        // 4 x 10 ms
#define MIC_VAD_END_BLOCKS    35       // 350 ms im lặng thì kết thúc đoạn nói
#define MIC_MAX_SEGMENT_BLOCKS 300     // tối đa 3 s/đoạn
#define MUTE_MIC_WHILE_SPEAKER 1       // tránh mic thu tiếng loa của chính máy
#define SPEAKER_VOLUME_PCT    80
#define SPEAKER_RING_SAMPLES  8192     // ~512 ms PCM mono 16 kHz

// ---------------------------------------------------------------------------
//  LCD 2004 I2C (HD44780 + PCF8574 backpack)
//  SDA=GPIO21, SCL=GPIO22. LCD_I2C_ADDRESS=0 nghia la tu do 0x27/0x3F;
//  neu module cua ban co dia chi co dinh khac, dien truc tiep o day.
//  Khong can cai LiquidCrystal_I2C vi sketch da co lcd_2004_i2c.h.
// ---------------------------------------------------------------------------
#define USE_LCD_2004          1        // 0 = tat hoan toan code LCD
#define PIN_I2C_SDA           21
#define PIN_I2C_SCL           22
#define LCD_I2C_ADDRESS       0        // 0=auto (0x27/0x3F), hoac 0x27/0x3F
#define LCD_I2C_FREQ_HZ       100000   // PCF8574 pho bien chi nen chay 100 kHz
#define LCD_UPDATE_MS         200      // chi ghi lai dong thay doi

// ---------------------------------------------------------------------------
//  Nhịp giao tiếp / an toàn
// ---------------------------------------------------------------------------
#define STATUS_PERIOD_MS      500
#define PROGRESS_PERIOD_MS    200
#define HELLO_PERIOD_MS       2000
#define CUP_RETRY_MS          2000
#define WAIT_PI_TIMEOUT_MS    15000
#define LINK_TIMEOUT_MS       3000     // khi đang bơm, Pi phải PING/PUMP_SET <3 s/lần
#define PIN_LED               2        // LED onboard của DevKit V1
#define USE_STATUS_LED        1

// ---------------------------------------------------------------------------
//  Kiểm tra chân lúc biên dịch
// ---------------------------------------------------------------------------
static constexpr bool piPinIsFlash(int p) { return p >= 6 && p <= 11; }
static constexpr bool piPinInputOnly(int p) { return p >= 34 && p <= 39; }

static_assert(!piPinIsFlash(PIN_PI_UART_RX) && !piPinIsFlash(PIN_PI_UART_TX),
              "UART2 khong duoc dung GPIO6..11 (flash)");
static_assert(!piPinInputOnly(PIN_PI_UART_RX) && !piPinInputOnly(PIN_PI_UART_TX),
              "UART2 khong duoc dung GPIO34..39 (input-only)");
static_assert(PIN_CUP_SENSOR == 34, "So do rev Pi: cam bien cb phai o GPIO34");
static_assert(!piPinInputOnly(PIN_START_BUTTON) &&
              !piPinInputOnly(LEVEL_BUTTON_PINS[0]) &&
              !piPinInputOnly(LEVEL_BUTTON_PINS[1]) &&
              !piPinInputOnly(LEVEL_BUTTON_PINS[2]),
              "Nut INPUT_PULLUP khong duoc o GPIO34..39");
static_assert(!piPinIsFlash(PIN_START_BUTTON) &&
              !piPinIsFlash(LEVEL_BUTTON_PINS[0]) &&
              !piPinIsFlash(LEVEL_BUTTON_PINS[1]) &&
              !piPinIsFlash(LEVEL_BUTTON_PINS[2]),
              "Nut khong duoc o GPIO6..11");
static_assert(!piPinIsFlash(PIN_RELAY) && !piPinInputOnly(PIN_RELAY),
              "Relay phai la chan xuat duoc muc logic");
static_assert(!piPinIsFlash(PIN_I2S_BCLK) && !piPinIsFlash(PIN_I2S_LRCLK) &&
              !piPinIsFlash(PIN_I2S_MIC_SD) && !piPinIsFlash(PIN_I2S_SPK_DIN),
              "I2S khong duoc dung GPIO6..11");
static_assert(PIN_I2S_MIC_SD != PIN_I2S_SPK_DIN,
              "Mic SD va loa DIN phai la HAI chan du lieu khac nhau");
static_assert(!piPinIsFlash(PIN_I2C_SDA) && !piPinIsFlash(PIN_I2C_SCL) &&
              !piPinInputOnly(PIN_I2C_SDA) && !piPinInputOnly(PIN_I2C_SCL),
              "I2C LCD khong duoc dung GPIO6..11 hoac GPIO34..39");
static_assert(PIN_I2C_SDA != PIN_I2C_SCL,
              "I2C SDA va SCL phai la hai chan khac nhau");
static_assert(LCD_I2C_ADDRESS == 0 || LCD_I2C_ADDRESS == 0x27 ||
              LCD_I2C_ADDRESS == 0x3F,
              "LCD_I2C_ADDRESS chi nen la 0(auto), 0x27 hoac 0x3F");
static_assert(AUDIO_SAMPLE_RATE == 16000,
              "Ban Pi nay khoa PCM 16 kHz de vua ca hai chieu UART");

#endif  // CUPFILLER_PI_CONFIG_H
