# ESP32 Pi Link — bản ESP32 nối Raspberry Pi

Đây là chương trình ESP32 mới cho mạch rev Pi. Nó **không thay/xoá bản PC cũ** trong
`../esp32_cup_filler/`; hai bản dùng chung định dạng khung và phần lớn mã gói
trong `protocol.h`.

ESP32 đảm nhiệm phần cứng thời gian thực:

- cảm biến cốc;
- 3 nút mức + nút START;
- relay bơm và các chốt an toàn tại chỗ;
- mic INMP441: gửi PCM lên Pi;
- loa MAX98357A: phát PCM Pi gửi xuống.

Raspberry Pi đảm nhiệm camera/YOLO, quyết định `CUP_OK`, mức đích và lệnh
`START_FILL` / `PUMP_SET` / `STOP_FILL`.

## 1. Sơ đồ chân đã chốt theo ảnh mạch

| Chức năng | GPIO ESP32 | Ghi chú |
|---|---:|---|
| Cảm biến cốc `cb` | **34** | input-only, không có pull-up nội |
| Nút mức 1 | **32** | 30%, active LOW / `INPUT_PULLUP` |
| Nút mức 2 | **33** | 60%, active LOW / `INPUT_PULLUP` |
| Nút mức 3 | **25** | 90%, active LOW / `INPUT_PULLUP` |
| Nút START (`button4`) | **26** | active LOW / `INPUT_PULLUP` |
| Relay bơm | **27** | **HIGH = bật bơm** |
| UART2 RX lên Pi | **16** | nhận từ Pi TXD |
| UART2 TX lên Pi | **17** | gửi tới Pi RXD |
| I2S BCLK/SCK | **18** | dùng chung mic + loa |
| I2S LRCLK/WS | **19** | dùng chung mic + loa |
| INMP441 SD/DOUT | **23** | mic -> ESP32 |
| MAX98357A DIN | **13\*** | ESP32 -> loa |
| I2C SDA/SCL dự phòng | 21/22 | bản này chưa dùng |

> \* Ảnh crop không hiện net `DIN` của MAX98357A nối vào chân ESP nào. Code đang đặt
> `PIN_I2S_SPK_DIN = 13` trong `pi_config.h`. Hãy đối chiếu sơ đồ đầy đủ; nếu DIN ở
> chân khác thì chỉ sửa hằng số này. **DIN loa và SD mic phải là hai chân khác nhau**.

MAX98357A trong sơ đồ có `SD -> 3V3`: đúng, ampli bật và chọn kênh trái. Firmware
gửi cùng một mẫu ra cả L/R nên vẫn nghe bình thường. INMP441 mặc định được hiểu là
`L/R -> GND` (kênh trái). Nếu chân L/R của mic nối 3V3, đổi `MIC_CHANNEL_LEFT`
trong `pi_config.h` thành `0`.

### Cảnh báo riêng cho GPIO34

GPIO34 không có điện trở kéo lên/xuống nội và chỉ chịu tối đa 3V3:

- module cảm biến phải xuất mức logic **0/3V3**;
- nếu ngõ ra là open-collector hoặc công tắc kéo xuống GND, phải có điện trở
  **10 kΩ từ GPIO34 lên 3V3** ở phần cứng;
- không đưa thẳng tín hiệu 5V vào GPIO34;
- mặc định code coi **LOW = có cốc**. Nếu cảm biến của bạn là HIGH khi có cốc,
  đổi `SENSOR_ACTIVE_LOW` trong `pi_config.h` thành `0`.

## 2. Nối ESP32 với Raspberry Pi

Header H1 trên sơ đồ:

| H1 ESP32 | Nối sang Pi |
|---|---|
| 1 — TX2 / GPIO17 | Pi RXD, GPIO15, chân vật lý 10 |
| 2 — RX2 / GPIO16 | Pi TXD, GPIO14, chân vật lý 8 |
| 3 — GND | Pi GND, ví dụ chân vật lý 6 |

Bật UART của Pi và tắt serial console (`sudo raspi-config` -> Interface Options ->
Serial Port: login shell **No**, serial hardware **Yes**). Cổng thường là
`/dev/serial0`.

UART đặt theo yêu cầu:

```text
961000 baud, 8N1, không flow-control
```

Pi phải mở đúng `961000`. Nếu bản OS/driver của Pi không chấp nhận baud lẻ này,
đổi **cả hai phía** sang `921600` tại `PI_UART_BAUD` — băng thông vẫn dư.

## 3. Thứ tự byte trạng thái đúng yêu cầu

ESP32 gửi gói mới `INPUT_STATE`, mã gói `0x10`, khi bất kỳ ngõ vào nào đổi trạng
thái và nhắc lại mỗi 100 ms. Payload có đúng 5 byte:

```text
byte 0: cảm biến          0x01 = có cốc/tác động, 0x00 = không
byte 1: START GPIO26      0x01 = đang nhấn,       0x00 = thả
byte 2: mức 1 GPIO32      0x01 = đang nhấn,       0x00 = thả
byte 3: mức 2 GPIO33      0x01 = đang nhấn,       0x00 = thả
byte 4: mức 3 GPIO25      0x01 = đang nhấn,       0x00 = thả
```

Đây là **byte nhị phân** `0x00/0x01`, không phải ký tự ASCII `'0'/'1'`. Toàn bộ
gói vẫn dùng khung cũ để Pi tách được khỏi PCM và kiểm lỗi:

```text
AA 55 | LEN | SEQ | MSG=0x10 | 5 byte trên | CRC16 low | CRC16 high
```

Ví dụ đã kiểm bằng host-test (SEQ=0x2A, payload `01 00 01 00 01`):

```text
AA 55 07 2A 10 01 00 01 00 01 5A FC
```

Ngoài snapshot 0/1 ở trên, ESP còn gửi:

- `LEVEL_SELECTED (0x11)`: `index, percent, source`;
- đồng thời vẫn gửi `PRESET_SELECTED (0x04)` của bản cũ, trong đó ml là
  30/60/90% của `max_ml` hiện hành để code phía host cũ dùng lại được;
- `START_REQUEST (0x12)`: `selected_index, percent`;
- `BUTTON_EVENT (0x0B)` khi thả nút, index 0/1/2 là mức, index 3 là START.

## 4. Luồng máy trạng thái và an toàn

```text
đặt cốc
  -> INPUT_STATE(sensor=1) + CUP_PLACED
  -> Pi bật camera/nhận diện rồi gửi CUP_OK
  -> ESP mở khoá nút mức/START (kênh audio chạy độc lập)
chọn mức 30/60/90
  -> LEVEL_SELECTED
nhấn START
  -> INPUT_STATE(start=1) + START_REQUEST
  -> Pi gửi START_FILL, sau đó PUMP_SET/STOP_FILL theo mực nước camera
  -> ESP đóng/mở relay GPIO27 và gửi FILL_PROGRESS/FILL_DONE
```

ESP tự ngắt relay không chờ Pi khi:

- cảm biến mất cốc;
- đang bơm mà quá `LINK_TIMEOUT_MS = 3000 ms` không nhận frame nào từ Pi —
  vì vậy trong lúc bơm Pi phải gửi `PING` hoặc `PUMP_SET` ít nhất 1 lần/giây;
- giữ START hoặc một nút mức từ 2 giây;
- nhấn START lần nữa trong lúc đang bơm;
- ước lượng quá `max_ml` hoặc quá thời gian bơm.

START chỉ là **yêu cầu** khi `SET_MODE` đang bật chế độ Pi điều khiển bơm (mặc định).
Điều này giữ nguyên kết cấu cũ: ESP không tự bơm trước khi Pi xác nhận cốc.

## 5. Audio hai chiều trên cùng UART

Định dạng cả hai chiều là PCM **mono, signed 16-bit little-endian, 16000 Hz**,
mỗi gói tối đa 96 mẫu:

| Hướng | MSG | Payload |
|---|---:|---|
| Mic ESP32 -> Pi | `0x0A AUDIO_CHUNK` | `seq8, flags, n(u16), PCM...` |
| Loa Pi -> ESP32 | `0x8E SPEAKER_AUDIO` | `seq8, flags, n(u16), PCM...` |
| Điều khiển | `0x8F AUDIO_CTRL` | mic on, mic continuous, speaker on, volume % |

Băng thông mỗi chiều:

```text
PCM thô:       16000 mẫu/s * 2 byte = 32000 byte/s
Sau đóng khung: khoảng 33800 byte/s
UART 961000:   96100 byte/s mỗi chiều (8N1)
```

Vì UART song công toàn phần, mic lên và loa xuống dùng hai dây riêng. Mặc định mic
chỉ gửi khi VAD thấy tiếng nói. Gửi `AUDIO_CTRL` với `mic_continuous=1` nếu Pi cần
PCM liên tục. Khi loa đang phát, firmware mặc định tạm dừng gửi mic để Pi không nghe
lại chính tiếng loa; đổi `MUTE_MIC_WHILE_SPEAKER` thành `0` nếu cần song công thật.

Pi phải gửi PCM loa **đúng nhịp thời gian thực**. ESP chỉ đệm được 8192 mẫu
(~512 ms); gửi dồn cả file một lần sẽ làm tràn đệm và ESP bỏ mẫu thừa.

## 6. Nạp firmware

### Cách khuyến nghị: VS Code + PlatformIO

Mở thư mục `cup_filler_pc/pi_esp32` trong VS Code, chọn env `esp32dev_pi`, bấm
Build rồi Upload. Serial Monitor mở cổng USB của ESP32 ở **115200** để xem log;
kênh Pi ở UART2 độc lập với log.

### Arduino IDE

Mở `esp32_pi_link.ino` trong thư mục này. Tất cả header đi kèm phải giữ cùng thư mục,
và file `../esp32_cup_filler/protocol.h` vẫn phải tồn tại vì bản Pi dùng lại codec
khung cũ. Bản audio dùng legacy I2S driver như firmware cũ, vì vậy hãy dùng gói
board **esp32 by Espressif Systems 2.0.x** trong Arduino IDE (PlatformIO ở trên đã
ghim platform 6.x tương ứng).

## 7. Kiểm tra không cần phần cứng

Từ thư mục gốc repo:

```bash
g++ -std=c++11 -O2 -Wall \
  -I cup_filler_pc/firmware/esp32_cup_filler \
  -o /tmp/test_pi_protocol \
  cup_filler_pc/firmware/esp32_pi_link/host_test/test_pi_protocol.cpp
/tmp/test_pi_protocol
```

Kết quả mong đợi: `8 dung / 0 sai`, kèm ví dụ frame `INPUT_STATE` ở trên.

Có thêm `host_test/test_pi_run.cpp` chạy thật logic `.ino` trên máy ảo Arduino của
repo: nó kéo GPIO34/26/32/33/25 xuống LOW, giải mã frame ESP gửi ra và kiểm tra
relay GPIO27 chỉ bật sau `CUP_OK -> START_FILL`. Lệnh biên dịch đầy đủ nằm ở đầu file;
kết quả mong đợi là `10 dung / 0 sai`.

## 8. Thứ tự test an toàn trên mạch thật

1. Chưa nối bơm: nạp firmware, xem USB log, nghe tiếng bíp khởi động để xác nhận loa.
2. Chưa nối Pi cũng được: nhấn từng nút và che cảm biến; kiểm tra phía Pi/serial
   capture thấy payload đổi đúng thứ tự cảm biến, START, mức 1/2/3.
3. Nối Pi, cho Pi gửi `PING` và `CUP_OK`; chọn mức và nhấn START, xác nhận Pi nhận
   `LEVEL_SELECTED` + `START_REQUEST`, nhưng relay chỉ đóng sau `START_FILL/PUMP_SET`.
4. Nối relay với tải giả/LED trước, kiểm tra mất cốc và rút UART khi bơm đều ngắt.
5. Cuối cùng mới nối bơm thật và hiệu chuẩn `FLOW_ML_PER_S` trong `pi_config.h`.
