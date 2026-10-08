# Firmware ESP32 — máy rót nước tự động

ESP32 là "người gác cổng" của máy: nó **phát hiện cốc**, **xin phép máy tính**,
nhận **nút bấm / lệnh giọng nói** và **đóng relay bơm**. Mọi quyết định về
lượng nước vẫn do máy tính (camera + thuật toán) đưa ra.

```
        CAM BIẾN CÓC ──► ESP32 ──$CUP,1──► MÁY TÍNH (chỉ lúc này mới BẬT CAMERA)
                            ▲                     │ nhận diện cốc trong ảnh
     NÚT MỨC 1..5 ─────────►│◄────$ACK,1──────────┘ (không thấy -> $ACK,0)
     NÚT DỪNG ─────────────►│
     MIC / module giọng nói►│  ── ARMED: cho phép người dùng ra lệnh
                            │  ◄──$START,200──  (người dùng bấm nút / nói)
                            │  ──$PUMP,duty──► RELAY BƠM (tích cực CAO)
                            └──$HB / $STOP / $ERR──► máy tính
```

## 1) Quy ước điện — đã chốt theo yêu cầu

| Thiết bị | Chân | Mức tác động | Cấu hình chân |
|---|---|---|---|
| Relay bơm | GPIO23 → `IN` | **CAO** (1 = bơm chạy) | `OUTPUT`, khởi động ghi **0** |
| Cảm biến cốc | GPIO32 ← `OUT` | **THẤP** (0 = có cốc) | `INPUT_PULLUP` |
| Nút mức 1 (100 ml) | GPIO25 | **THẤP** | `INPUT_PULLUP` |
| Nút mức 2 (150 ml) | GPIO26 | **THẤP** | `INPUT_PULLUP` |
| Nút mức 3 (200 ml) | GPIO27 | **THẤP** | `INPUT_PULLUP` |
| Nút mức 4 (250 ml) | GPIO14 | **THẤP** | `INPUT_PULLUP` |
| Nút mức 5 (300 ml) | GPIO13 | **THẤP** | `INPUT_PULLUP` |
| Nút DỪNG | GPIO33 | **THẤP** | `INPUT_PULLUP` |
| LED trạng thái | GPIO2 (LED onboard) | CAO = sáng | `OUTPUT` |
| Còi beep (tuỳ chọn) | GPIO4 | CAO = kêu | `OUTPUT` |

Mỗi nút/cảm biến chỉ cần **nối một đầu vào GPIO, đầu còn lại nối GND** —
không cần điện trở ngoài vì đã bật pull-up bên trong.

> Tránh GPIO 6–11 (nối flash), GPIO 12 (strapping, kéo cao lúc khởi động sẽ
> lỗi nạp), GPIO 34–39 (không có pull-up nội).

### Sơ đồ nối

```
             ESP32 DevKit V1
        ┌───────────────────────┐
  GND ──┤GND               GPIO23├──► IN  [RELAY] ──► bơm 12 V (nguồn riêng)
  GND ──┤GND                GPIO2├──► LED + điện trở 330 Ω
  GND ──┤GND                GPIO4├──► còi buzzer (tuỳ chọn)
        │                        │
  cảm biến cốc (IR/cảm ứng) OUT ──┤GPIO32   VCC ← 3.3 V, GND ← GND
        │                        │
  nút 100 ml ────────────────────┤GPIO25
  nút 150 ml ────────────────────┤GPIO26
  nút 200 ml ────────────────────┤GPIO27      (đầu kia của mọi nút nối GND)
  nút 250 ml ────────────────────┤GPIO14
  nút 300 ml ────────────────────┤GPIO13
  nút DỪNG   ────────────────────┤GPIO33
        │                        │
  USB ──────┤USB          (UART0)├──────► MÁY TÍNH (115200 8N1)
        └───────────────────────┘
```

**Lưu ý về relay:** bơm/máy bơm 12 V phải dùng **nguồn riêng**, nối chung GND
với ESP32, và nên có diode bảo vệ (1N4007 song song cuộn hút relay). Relay chỉ
đóng/cắt chậm nên firmware **băm mềm (soft-PWM 400 ms)**; nếu muốn điều khiển
mịn ở pha rót rỉ cuối kỳ thì thay relay bằng **MOSFET** và giảm
`SOFTPWM_PERIOD_MS` xuống 20 ms trong `config.h`.

## 2) Nạp firmware

**Arduino IDE** (đơn giản nhất):

1. Cài board ESP32: *File → Preferences*, thêm
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`,
   rồi *Boards Manager* → tìm "esp32" → Install.
2. Mở `cup_filler_esp32.ino` (toàn bộ `.h`/`.cpp` trong thư mục này sẽ được
   biên dịch cùng).
3. Chọn board **ESP32 Dev Module**, cổng COM của mạch.
4. Bấm **Upload**.

**PlatformIO**:

```bash
cd cup_filler_pc/firmware/esp32
pio run -t upload --upload-port COM5
```

## 3) Chạy thử không cần phần cứng

Toàn bộ logic (cảm biến → nút/mic → relay, watchdog, ngắt bơm) nằm trong các
file **không phụ thuộc Arduino**, nên biên dịch và chạy được ngay trên PC:

```bash
cd cup_filler_pc/firmware/esp32
make test     # 88 kiểm tra đơn vị (giao thức + máy trạng thái + an toàn)
make sim      # tạo "ESP32 ảo" nối qua stdin/stdout
```

Sau đó chạy thử cả hệ thống trên máy tính:

```bash
python tools/run_esp.py --sim --synthetic --auto-test   # tự chạy hết kịch bản
python tools/run_esp.py --sim --monitor                 # xem khung tin, gõ !CUP 1
python tools/test_esp.py                                # kiểm chứng PC <-> ESP32
```

## 4) Giao thức UART (115200 8N1)

Khung tin dạng văn bản, đọc được trên Serial Monitor:

```
$TYPE[,arg1,arg2,...]*XX\n         XX = tổng các byte giữa '$' và '*', mod 256, hex thường
ví dụ:  $CUP,1*45
```

**ESP32 → máy tính**

| Tin | Ý nghĩa |
|---|---|
| `BOOT,<ver>` | vừa khởi động, hoặc vừa nhận `HELLO` |
| `HB,<ms>,<state>,<cup>,<armed>,<duty>` | nhịp sống, gửi mỗi 250 ms |
| `CUP,<0\|1>` | cảm biến cốc đổi trạng thái (đã lọc rung 150 ms) |
| `BTN,<id>,<DOWN\|LONG>` | nút bấm: id 1..5 = mức, 0 = DỪNG |
| `LVL,<ml>` | mức rót được chọn |
| `START,<ml>` | **người dùng yêu cầu rót** (nút bấm hoặc mic) |
| `STOP,<lý_do>` | dừng: `BTN_STOP` / `VOICE_STOP` / `CUP_REMOVED` / `PC_TIMEOUT` / `FILL_TIMEOUT` |
| `DONE,<ms>` | một lần rót kết thúc |
| `PUMP,<duty>` | tiếng vang mức bơm đang giữ (0..100) |
| `ERR,<mã>` | `NO_CUP_VISION` / `ACK_TIMEOUT` / `PC_TIMEOUT` / `NOT_ACKED` / `NOT_ARMED` / `BUSY` / `PC_FAULT` |
| `CFG,OK` | đã nhận danh sách mức rót từ máy tính |

**Máy tính → ESP32**

| Tin | Ý nghĩa |
|---|---|
| `HELLO,<ver>` | bắt tay; ESP32 trả lời `BOOT` + trạng thái hiện tại |
| `CFG,<ml;ml;...>` | danh sách mức rót (dùng `;` vì `,` là dấu phân cách) |
| `ACK,<0\|1>` | **camera có thấy cốc không** → cho phép / không cho phép rót |
| `PUMP,<0..100>` | mức bơm; ESP32 băm mềm ra relay |
| `STATE,<tên>` | trạng thái FSM máy tính; `DONE`/`FAULT` là quan trọng nhất |
| `HB,<ms>` | nhịp sống của máy tính — **ESP32 dùng làm watchdog (800 ms)** |

## 5) Máy trạng thái của ESP32

| Trạng thái | LED | Hành vi |
|---|---|---|
| `IDLE` | chớp chậm 1 Hz | chỉ đọc cảm biến. Có cốc **và** máy tính đang online → gửi `CUP,1` |
| `WAIT_ACK` | chớp nhanh 5 Hz | chờ `ACK,1`. Quá 6 s → `ERR,ACK_TIMEOUT` |
| `ARMED` | sáng đứng | **cho phép** nút bấm / mic. Bấm nút mức = chọn + bắt đầu rót; **giữ lâu** = chỉ đổi mức |
| `FILLING` | chớp theo nhịp bơm | relay chạy theo `PUMP,duty`. Chỉ nhận nút DỪNG |

Các chốt an toàn (đều có kiểm tra đơn vị trong `test/test_firmware.cpp`):

* Relay được ghi **0 ngay trong `begin()`** → bơm không tự chạy lúc nạp firmware.
* Chưa có `ACK,1` thì **mọi** yêu cầu rót đều bị từ chối (`ERR,NOT_ACKED`).
* Nhả cốc giữa chừng → **cắt relay trước**, báo `STOP,CUP_REMOVED` sau.
* Máy tính im lặng quá 800 ms → cắt relay, `STOP,PC_TIMEOUT`, về `IDLE`.
* Máy tính báo "không thấy cốc" 3 lần liên tiếp → ngừng báo lại, **chờ nhấc cốc ra**
  rồi đặt lại (tránh spam UART).
* Một lần rót kéo dài quá `FILL_MAX_MS` (60 s) → tự cắt bơm.

## 6) Mic / nhận lệnh giọng nói

`VOICE_MODE` trong `config.h`:

| Giá trị | Cách dùng | Trạng thái |
|---|---|---|
| `0` | không dùng mic | đã kiểm chứng |
| `1` | module giọng nói xuất **xung GPIO** (SU-03T chế độ GPIO): mỗi câu lệnh một chân, tích cực THẤP + pull-up — chân 5/18/19/21/22/34 lần lượt là *mức 1..5* và *"rót nước"* | **mặc định**, đã kiểm chứng bằng unit test |
| `2` | module giọng nói truyền **UART** (SU-03T/LD3320) ở GPIO16/17, 9600 baud. Mỗi câu lệnh gửi một mã ngắn (`A1`..`A5`, `B1` = rót, `B2` = dừng) — sửa bảng `kVoiceCodes` trong `voice_input.cpp` theo cấu hình module của bạn | cần phần cứng để kiểm chứng |
| `3` | **ESP-SR** trên ESP32-S3 + mic I2S INMP441 (xem `voice_input_espsr.cpp`, bật bằng `-DUSE_ESP_SR=1`) | tuỳ chọn, chưa kiểm chứng trong repo này |

Với tiếng Việt, cách thực tế nhất là **module SU-03T** (học lệnh offline, cấu
hình bằng phần mềm của hãng, xuất xung/UART ra ESP32) — ESP-SR của Espressif
chủ yếu hỗ trợ tiếng Anh/Trung. Dù dùng đường nào, phần còn lại của firmware
**không đổi**: tất cả quy về một mã lệnh `VoiceCmd` → chọn mức / rót / dừng,
giống hệt nút bấm.

## 7) Sửa cấu hình

Mọi tham số nằm trong `config.h` (số chân, thời gian lọc rung, thời gian chờ,
chu kỳ băm relay, mức cốc mặc định). Danh sách mức rót còn có thể bị máy tính
ghi đè lúc chạy bằng tin `CFG`.

## 8) Cấu trúc thư mục

```
firmware/esp32/
├── cup_filler_esp32.ino      # vòng lặp Arduino (setup/loop)
├── config.h                  # CHÂN + THAM SỐ (sửa ở đây)
├── device_fsm.h/.cpp         # máy trạng thái: cảm biến, nút, mic, relay, watchdog
├── uart_protocol.h/.cpp      # đóng/gói khung tin + CRC (không phụ thuộc Arduino)
├── voice_input.h/.cpp        # đầu vào giọng nói (GPIO / UART / ESP-SR)
├── voice_input_espsr.cpp     # tuỳ chọn ESP32-S3 + ESP-SR
├── board_io.h                # lớp che GPIO
├── board_io_arduino.cpp      #   ... cho ESP32 thật
├── link_io.h                 # lớp che UART
├── link_io_arduino.cpp       #   ... cho ESP32 thật
├── host/                     # mô phỏng trên PC (g++): GPIO ảo + "ESP32 ảo"
├── test/test_firmware.cpp    # 88 kiểm tra đơn vị
├── Makefile                  # make test / make sim
└── platformio.ini
```
