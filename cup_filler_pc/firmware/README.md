# 🔌 Firmware ESP32 — máy rót nước (UART với máy tính)

Bo mạch ESP32 lo phần **cảm biến – nút bấm – mic – bơm**; **máy tính lo nhận diện cốc**.
Hai bên nói chuyện với nhau qua **UART** (khung gói `AA 55 … CRC`, xem `docs/PROTOCOL.md`).

```
   ┌─────────────── ĐẶT CỐC VÀO KHAY ───────────────┐
   │                                                ▼
 ┌────────┐  CUP_PLACED (UART)   ┌───────────────────────────┐
 │ ESP32  │ ───────────────────► │  MÁY TÍNH (PC)            │
 │ HC-SR04│                      │  1. BẬT CAMERA            │
 │ 5 nút  │ ◄─────────────────── │  2. NHẬN DIỆN CỐC         │
 │ mic    │  CUP_OK (đã nhận ra) │  3. gửi hình học/thể tích │
 │ relay  │                      └───────────────────────────┘
 └───┬────┘
     │  CUP_OK đến  →  MỞ KHOÁ nút bấm + mic
     │  người dùng bấm nút / nói
     ▼
  RELAY (mức CAO = bật) → BƠM NƯỚC   ...  nhấc cốc → ESP tự ngắt bơm ngay
```

**Điểm mấu chốt theo yêu cầu thiết kế:** trước khi PC gửi `CUP_OK`, mọi thao tác bấm nút
hay nói vào mic đều **bị bỏ qua** (đèn LED nháy nhanh ở trạng thái `WAIT_PC`). Chỉ sau khi
PC xác nhận "đã thấy cốc, rót được tối đa N ml" thì nút/mic mới có tác dụng và mới bật bơm.

---

## 1) Danh sách linh kiện

| # | Linh kiện | Ghi chú |
|---|---|---|
| 1 | **ESP32 DevKit V1** (ESP32-WROOM-32, board 30 chân) | chọn board **"ESP32 Dev Module"** trong Arduino IDE; sơ đồ chân bên dưới đã tính sẵn cho board này |
| 2 | Module relay 1 kênh **tích cực mức CAO** | nếu module là loại tích cực mức thấp, đổi `RELAY_ACTIVE_HIGH` thành `0` |
| 3 | Bơm nước mini DC (3–6 V hoặc 12 V tuỳ loại) | **nguồn riêng** cho bơm, không lấy từ ESP32 |
| 4 | 5 nút nhấn (loại 4 chân hoặc 2 chân) | một chân nối **GND**, chân kia vào GPIO |
| 5 | HC-SR04 (siêu âm) **hoặc** cảm biến hồng ngoại / công tắc hành trình | mặc định dùng HC-SR04 gắn **trên** khay |
| 6 | Mic: MAX9814 / MAX4466 (analog) **hoặc** INMP441 / ICS-43434 (I2S) | analog đơn giản hơn |
| 7 | Mạch USB-TTL (CH340/CP2102) | nối UART2 của ESP32 với máy tính; 3V3 logic, **nối chung GND**. **Không cần** nếu bạn đặt `UART_USE_USB_SERIAL 1` (xem §3b) |
| 8 | Ống dẫn nước + khay đặt cốc | đặt cốc cùng vị trí mỗi lần để cảm biến ổn định |

> ⚠️ **Nguồn điện:** bơm là tải cảm (có mô-tơ) → **không** cấp trực tiếp từ chân 3V3/5V của ESP32.
> Dùng nguồn riêng, nối GND chung với ESP32, và thêm diode flyback (1N4007) song song với bơm nếu
> module relay chưa có.

---

## 2) Sơ đồ chân (mặc định trong `config.h`)

| Chức năng | GPIO | Ghi chú |
|---|---|---|
| Nút 100 ml | **32** | nút nối **GND**, đọc bằng `INPUT_PULLUP` → nhấn = mức THẤP |
| Nút 150 ml | **33** | 〃 |
| Nút 200 ml | **25** | 〃 |
| Nút 250 ml | **26** | 〃 |
| Nút 300 ml | **27** | 〃 |
| Relay bơm | **23** | **mức CAO = BẬT bơm** (`RELAY_ACTIVE_HIGH 1`) |
| HC-SR04 TRIG | **5** | chân "strapping" — có kéo lên nội, chân TRIG của HC-SR04 là ngõ vào nên vẫn an toàn. Nếu bo không khởi động được, đổi sang **4** |
| HC-SR04 ECHO | **18** | ngõ vào, chịu được 5 V; nếu dùng loại HC-SR04 3.3 V thì nối thẳng |
| Mic analog (MAX9814 OUT) | **35** | chỉ đọc, ADC1 (đọc được cả khi bật WiFi), mic ~1/2 VCC khi im lặng |
| Cảm biến cốc loại số (IR/công tắc) | **19** | chỉ dùng khi `CUP_SENSOR_TYPE 1/2`; `begin()` đặt `INPUT_PULLUP` nên **phải** là chân có kéo lên nội |
| Mic I2S SCK / WS / SD | **14 / 15 / 21** | chỉ dùng khi `MIC_TYPE 2`; **đừng** dùng GPIO33 (đã là nút 150 ml) |
| UART2 RX | **16** | ← TX của mạch USB-TTL |
| UART2 TX | **17** | → RX của mạch USB-TTL (nhớ nối chéo RX↔TX) |
| LED báo trạng thái | **2** | LED có sẵn trên board devkit |

### ⚠️ Những chân KHÔNG được dùng trên ESP32 DevKit V1

| GPIO | Vấn đề | Ảnh hưởng |
|---|---|---|
| 6, 7, 8, 9, 10, 11 | nối thẳng vào chip flash trong | dùng là **bo không khởi động được** |
| 34, 35, 36, 39 (VP/VN) | **chỉ đọc**, không có điện trở kéo lên nội | đọc ADC (mic) thì tốt; nút bấm `INPUT_PULLUP` thì **không chạy** |
| 0 (BOOT), 2 (LED xanh), 5, 12, 15 | chân *strapping* — quyết định lúc cấp điện | để mạch ngoài kéo xuống GND lúc bật nguồn là **không boot / không nạp được** |
| 0, 2, 4, 12, 13, 14, 15, 25, 26, 27 | thuộc **ADC2** | `analogRead()` không đọc được khi WiFi đang bật → mic phải ở **ADC1 (32–39)** |

`config.h` đã được đặt sẵn **đúng theo các quy tắc trên**, và có `static_assert` ở cuối file:
nếu bạn đổi chân sai (ví dụ đặt relay hay nút vào GPIO 34–39) thì Arduino IDE báo lỗi ngay khi
biên dịch, chứ không phải chạy mới phát hiện. Bộ kiểm tra trên PC cũng kiểm lại:
`python3 tools/test_comms.py --only firmware` (không dùng chân flash, không trùng chân, nút không
ở chân chỉ-đọc, mic ở ADC1).

Sơ đồ nối UART với máy tính:

```
ESP32 GPIO16 (RX2) ◄──── TXD  mạch USB-TTL  ────► cổng USB của PC
ESP32 GPIO17 (TX2) ────► RXD
ESP32 GND ────────────── GND           (BẮT BUỘC nối chung GND)
```

Nối nút bấm (làm 5 cái giống nhau):

```
GPIO32 ──┬── nút 100 ml ──┐
GPIO33 ──┼── nút 150 ml ──┤
GPIO25 ──┼── nút 200 ml ──┼── tất cả nối vào GND chung
GPIO26 ──┼── nút 250 ml ──┤    (INPUT_PULLUP -> nhấn = mức THẤP, không cần trở ngoài)
GPIO27 ──┴── nút 300 ml ──┘
```

Relay và bơm (relay **tích cực mức CAO**):

```
GPIO23 ──── IN  (module relay 1 kênh, VCC 5 V lấy từ nguồn riêng hoặc chân 5V của board)
COM của relay ──── + nguồn bơm (nguồn RIÊNG, đủ dòng cho bơm)
NO  của relay ──── + của bơm        GND bơm ──── GND chung với ESP32
```

LED báo trạng thái: nháy chậm = chờ đặt cốc · **nháy nhanh = chờ PC xác nhận cốc** ·
**sáng đều = đã mở khoá, bấm nút được** · nháy gấp = đang bơm · nháy rất nhanh = lỗi.

---

## 2b) Các file trong `firmware/` — file nào làm gì

| File | Nội dung |
|---|---|
| **`cup_filler_pc/serial_esp32/include/serial_link.h`** | **Phần GIAO TIẾP VỚI PC (file riêng, nằm trong thư mục project VS Code + PlatformIO)**: chọn cổng (UART2 GPIO16/17 hay cáp USB của board), mở cổng, đóng khung gói tin, gửi mọi loại tin (`link.status()`, `link.error()`, `link.log()`...), đọc byte và tách khung rồi đưa từng khung cho `.ino` (`link.poll()`). Muốn xem/sửa cách ESP nói chuyện với PC thì mở **file này**. |
| `esp32_cup_filler/esp32_cup_filler.ino` | Logic của máy: cảm biến cốc, nút bấm, mic, bơm/relay, máy trạng thái, các điều kiện an toàn. Chỉ gọi `link.…` chứ không tự đọc/ghi serial. |
| `esp32_cup_filler/config.h` | Chân GPIO, tốc độ bơm, preset ml, chọn kênh UART2 hay cáp USB (`UART_USE_USB_SERIAL`)... — **sửa theo máy của bạn**. |
| `esp32_cup_filler/protocol.h` | Mã hoá/giải mã từng byte của giao thức (khớp với `cupfiller/protocol.py` bên PC). |
| `esp32_cup_filler/esp32_cup_filler_usb.cpp` | Bản dùng **cáp USB của board**: bật `UART_USE_USB_SERIAL 1` + `DEBUG_SERIAL 0` rồi `#include` file `.ino` ở trên (chọn môi trường `esp32dev_usb`, xem §3). |
| `esp32_cup_filler/cup_sensor.h`, `buttons.h`, `pump.h`, `voice_mic.h` | Driver: cảm biến siêu âm, 5 nút bấm, bơm qua relay, mic + đếm tiếng động. |
| `firmware/esp32_cup_filler/serial_link.h` | Cầu nối 3 dòng trỏ sang file thật ở `serial_esp32/include/` — chỉ để **Arduino IDE** (chỉ tìm file trong thư mục sketch) vẫn biên dịch được. |
| `cup_filler_pc/serial_esp32/` | **Project VS Code + PlatformIO**: `platformio.ini`, thư mục `include/` (chứa `serial_link.h`), `.vscode/`, README riêng. |
| `host_test/` | Bộ test chạy trên máy tính: `test_protocol.cpp` (vector vàng) và `test_firmware_run.cpp` (chạy THẬT `.ino` bằng g++, không cần phần cứng). |

---

## 3) Nạp firmware (Arduino IDE)

1. Cài **Arduino IDE 2.x**.
2. `File ▸ Preferences ▸ Additional Boards Manager URLs` thêm:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
3. `Tools ▸ Board ▸ Boards Manager` → tìm **esp32** (Espressif Systems) → **Install** (bản 2.x hoặc 3.x).
4. Mở file **`firmware/esp32_cup_filler/esp32_cup_filler.ino`** (Arduino sẽ tự mở kèm các `.h` cùng thư mục).
5. `Tools`:
   * Board: **ESP32 Dev Module**
   * Upload Speed: 921600
   * Flash Size: 4MB (mặc định)
   * Partition Scheme: Default
   * **Port**: cổng COM của ESP32 (cắm cáp USB trực tiếp vào board)
6. Bấm **Upload** (mũi tên →). Nếu báo lỗi `Failed to connect` → **giữ nút BOOT** trên board khi nó
   hiện `Connecting...` rồi thả ra.
7. Mở **Serial Monitor** (115200 baud) để xem log tiếng Việt của firmware.

### 3b) Không có mạch USB-TTL? Dùng luôn cổng USB của board

Board ESP32 DevKit V1 đã có sẵn chip USB-UART nối vào **UART0** (cổng USB). Firmware có thể nói
chuyện với phần mềm PC qua chính cổng đó, **không cần thêm linh kiện nào**:

1. Trong `config.h` sửa:
   ```c
   #define UART_USE_USB_SERIAL 1   // nói chuyện với PC qua cáp USB của board
   #define DEBUG_SERIAL        0   // BẮT BUỘC: một cổng USB không thể vừa log vừa gửi gói tin
   ```
   (Đặt `UART_USE_USB_SERIAL 1` mà quên `DEBUG_SERIAL 0` thì Arduino IDE báo lỗi ngay khi biên dịch.)
2. Nạp firmware xong, **cắm cáp USB vào máy tính** rồi chạy như bình thường:
   ```bash
   python3 tools/web.py --port COM5        # Windows: xem cổng ở Device Manager ▸ Ports
   python3 tools/esp_cli.py --port COM5    # hoặc dòng lệnh
   ```
3. Lưu ý: mỗi lần phần mềm PC **mở cổng COM**, board bị reset (do chân DTR/RTS) — firmware khởi
   động lại rồi tự gửi `HELLO`, chỉ chậm khoảng 0.5 s. Khi đang chạy mà bạn mở Serial Monitor
   thì cũng sẽ làm board reset và làm nhiễu gói tin → **đóng Serial Monitor** khi dùng chương trình PC.

> Bộ test trên PC chạy **cả hai** cấu hình này: `python3 tools/test_comms.py --only fwrun`
> sẽ biên dịch và chạy 45 bài kiểm tra hai lần (UART2 và cổng USB).

### Nạp bằng VS Code + PlatformIO (khuyến nghị nếu bạn viết code trên VS Code)

Project PlatformIO nằm ở thư mục riêng **`cup_filler_pc/serial_esp32/`**
(`platformio.ini` + `include/serial_link.h` + `.vscode/`), mã nguồn firmware vẫn ở
`firmware/esp32_cup_filler/` — `platformio.ini` trỏ `src_dir` sang đó nên **không có bản sao nào**:

1. Cài extension **PlatformIO IDE** trong VS Code (`Ctrl+Shift+X` → tìm *PlatformIO IDE*).
2. `File ▸ Open Folder...` → chọn thư mục **`cup_filler_pc/serial_esp32`** (chính thư mục có
   `platformio.ini`). PlatformIO sẽ tự tải ESP32 platform + toolchain trong lần build đầu
   (vài trăm MB, chỉ một lần).
3. Ở **thanh trạng thái dưới cùng**, chọn môi trường:
   * **`esp32dev`** – mặc định: ESP nói chuyện với PC qua UART2 (GPIO16/17) + mạch USB-TTL,
     còn cổng USB của board dùng để xem log.
   * **`esp32dev_usb`** – khi **không có mạch USB-TTL**: ESP nói chuyện với PC bằng chính
     cổng USB của board. Env này biên dịch **file mới `esp32_cup_filler_usb.cpp`**, trong đó
     bật `UART_USE_USB_SERIAL=1` + `DEBUG_SERIAL=0` rồi `#include` bản `.ino` cũ — nên
     **mọi gói tin (HELLO, CUP_OK, AUDIO, PRESS_BUTTON...) đều đi bằng `Serial.write()`**
     và bạn không cần mạch chuyển đổi USB-TTL.
4. Bấm **✓ Build** rồi **→ Upload**. Xem log: biểu tượng **phích cắm** (Serial Monitor) –
   `esp32dev`: 115200 baud · `esp32dev_usb`: 921600 baud và **đừng mở khi đang chạy
   phần mềm PC** (mở là board bị reset + nhiễu gói tin).

**Hai bản firmware – cùng một mã nguồn:**

| Chọn env | File được biên dịch | Kênh nói chuyện với PC | Cần gì thêm |
|---|---|---|---|
| `esp32dev` (mặc định) | `esp32_cup_filler.ino` (file cũ, giữ nguyên) | UART2 – GPIO16 (RX) / GPIO17 (TX), 921600 baud | mạch USB-TTL (CH340/CP2102/FT232) |
| `esp32dev_usb` | `esp32_cup_filler_usb.cpp` (file mới) | cổng USB có sẵn trên board (CH340/CP2102), 921600 baud | **không cần gì** – chỉ cáp USB |

Hai bản **không phải hai bản sao**: file mới chỉ bật 2 macro rồi `#include` file `.ino` cũ,
nên sửa code chỉ sửa **một chỗ** (`esp32_cup_filler.ino` + các `.h`) và cả hai bản cùng đổi.
`platformio.ini` dùng `build_src_filter` để mỗi env chỉ biên dịch **một** bản (không bị
trùng `setup()`/`loop()`). Phía PC không đổi gì: `python3 tools/web.py --port COM5`
(cổng COM của board khi cắm cáp USB - xem trong Device Manager).

Lệnh tương đương trong terminal VS Code:

```bash
pio run                       # biên dịch (env esp32dev)
pio run -t upload             # nạp
pio device monitor            # xem log tiếng Việt (115200)
pio run -e esp32dev_usb -t upload    # nạp bản dùng cổng USB của board
pio run -t erase               # xoá flash (khi board "cứng đầu", nạp mãi không được)
```

Vài lưu ý khi dùng PlatformIO:

* Chỉ thư mục **`firmware/esp32_cup_filler/`** được build (`src_dir` trong `platformio.ini`),
  `host_test/` là bộ test chạy trên máy tính nên **không** bị PlatformIO biên dịch.
* Phần giao tiếp với PC nằm ở **`serial_esp32/include/serial_link.h`**; PlatformIO tự thêm
  thư mục `include/` của project vào đường tìm kiếm `#include`.
* Sau mỗi lần build, PlatformIO sinh file `esp32_cup_filler.ino.cpp` cạnh file `.ino`
  (nó chuyển `.ino` → `.cpp` để sinh prototype cho `setup()`, `loop()`...). File này đã
  được `.gitignore` bỏ qua — **đừng sửa tay**, sửa code trong `.ino` và các `.h`.
* `pio device monitor` ở env `esp32dev_usb` sẽ làm board reset và hiện dữ liệu nhị phân —
  đó là kênh dữ liệu 921600, không phải log (bản này tắt log để không lẫn vào gói tin).
* Ở env `esp32dev_usb`, file `.cpp` do PlatformIO sinh ra từ `.ino` bị `build_src_filter`
  loại bỏ, chỉ file `esp32_cup_filler_usb.cpp` được biên dịch. Đừng xoá dòng
  `build_src_filter` trong `platformio.ini`, nếu không sẽ có **hai** `setup()`/`loop()`.
* Bộ test trên PC cũng kiểm tra luôn file `platformio.ini`, `.vscode/`, và **mô phỏng đúng
  bước sinh prototype của PlatformIO** rồi biên dịch file `.cpp` đó (nếu máy có cài
  PlatformIO): `python3 tools/test_comms.py --only firmware`.

Nếu vẫn thích nạp bằng Arduino IDE thì dùng hướng dẫn ở §3 phía trên (Board: **ESP32 Dev
Module** — cùng một board, hai cách nạp, dùng chung `config.h`). Với Arduino IDE, muốn dùng
cáp USB thay cho mạch USB-TTL thì sửa **trực tiếp `config.h`**: `UART_USE_USB_SERIAL 1` và
`DEBUG_SERIAL 0` (file `esp32_cup_filler_usb.cpp` chỉ có tác dụng khi build bằng PlatformIO).
Chi tiết về project VS Code + PlatformIO (chọn môi trường, sửa file serial, lỗi hay gặp):
`cup_filler_pc/serial_esp32/README.md`.

> Firmware chỉ dùng `Arduino.h` + thư viện chuẩn của core ESP32. Nếu bạn đổi `MIC_TYPE` sang **2**
> (mic I2S), một số bản core ESP32 3.x mới đã bỏ `driver/i2s.h` — khi đó hãy để `MIC_TYPE 1`
> (mic analog) hoặc xem ghi chú ở đầu file `voice_mic.h`.

---

## 4) Cài đặt phần cứng của bạn — sửa `config.h`

Toàn bộ thông số "phải chỉnh theo máy thật" nằm trong **`firmware/esp32_cup_filler/config.h`**:

| Hằng số | Ý nghĩa | Khi nào đổi |
|---|---|---|
| `BUTTON_PINS[]`, `PRESET_ML[]` | chân nút ↔ mức nước | khi đi dây khác |
| `RELAY_ACTIVE_HIGH` | `1` = mức CAO bật bơm | nếu module relay loại tích cực mức thấp → `0` |
| `CUP_SENSOR_TYPE` | `0` HC-SR04, `1` hồng ngoại, `2` công tắc hành trình | tuỳ linh kiện |
| `CUP_BASELINE_MM` | khoảng cách khi **khay trống** (mm) | đo lúc khởi động, tinh chỉnh sau khi lắp |
| `CUP_MIN_HEIGHT_MM` | chiều cao tối thiểu coi là "có cốc" | tránh nhận nhầm vật mỏng |
| `MIC_TYPE` | `0` không mic, `1` analog, `2` I2S | tuỳ mic |
| `VOICE_MODE` | `2` = gửi PCM lên PC nhận dạng, `1` = ESP tự đếm "tiếng" | xem §6 |
| `FLOW_ML_PER_S` | lưu lượng bơm thật (ml/s) khi 100 % | **hiệu chuẩn** theo §5 |
| `MAX_FILL_ML` | trần tuyệt đối, ESP tự ngắt | an toàn |
| `LINK_TIMEOUT_MS` | mất UART bao lâu thì ngắt bơm | an toàn |
| `UART_USE_USB_SERIAL` | `1` = dùng luôn cổng USB của board làm kênh nối PC (không cần USB-TTL). Env `esp32dev_usb` tự bật macro này trong `esp32_cup_filler_usb.cpp` | khi không có mạch USB-TTL — xem §3b |
| `DEBUG_SERIAL` | `1` = in log ra cổng USB để xem trên Serial Monitor | phải để `0` nếu `UART_USE_USB_SERIAL 1` |

Sau khi sửa `config.h` → nạp lại. Chạy `python3 tools/test_comms.py --only firmware` trên PC sẽ
kiểm tra nhanh xem `config.h` có khớp với cấu hình bên PC không (preset, cờ, chân...).

---

## 5) Hiệu chuẩn lưu lượng (`FLOW_ML_PER_S`) — để rót đúng ml

Firmware đong nước bằng **thời gian bơm × lưu lượng** nên con số này quyết định độ chính xác.

1. Cho bơm chạy **đúng 10 giây** vào cốc có chia vạch (dùng nút giữ lâu để chọn mức rồi `START_FILL`
   từ PC, hoặc tạm thời đặt `BUTTON_STARTS_POUR true` và bấm nút trong khi cốc đã được xác nhận).
2. Đo lượng nước thu được, ví dụ **410 ml / 10 s** → `FLOW_ML_PER_S = 41.0`.
3. Ghi vào `config.h`, nạp lại.
4. Nếu bơm chạy yếu ở mức thấp, giữ `FLOW_MIN_DUTY_PCT` (mặc định 8 %).

Mẹo: có thể hiệu chuẩn từ PC mà không cần nạp lại — gửi `SET_PARAMS`:

```bash
python3 tools/esp_cli.py --port COM5
> params 41.0 500 60          # flow ml/s, max ml, timeout giây
```

Giá trị này **không lưu vào bộ nhớ ESP** (bản firmware này chưa dùng NVS) — mỗi lần khởi động lại
ESP sẽ lấy theo `config.h`. Muốn cố định thì sửa `config.h`.

---

## 6) Mic & giọng nói — hai chế độ

| Chế độ | Cách chạy | Ưu / nhược |
|---|---|---|
| `VOICE_MODE 2` (mặc định) | ESP gửi PCM 16 kHz lên PC (`AUDIO_CHUNK`, có cờ START/END), **PC nhận dạng** (Vosk tiếng Việt nếu có, không thì đếm "tiếng") | hiểu được câu "rót hai trăm ml"; cần dây UART nhanh 921600 |
| `VOICE_MODE 1` | ESP tự đếm số "tiếng" (âm tiết) rồi chọn mức: 1 tiếng = 100 ml, 2 tiếng = 150 ml… | chạy được cả khi PC bận; không hiểu câu nói |

Cách dùng: sau khi PC báo `CUP_OK` (nút/mic đã mở khoá), bạn **nói "một … hai … ba"** (mỗi tiếng
cách nhau ~0,2 s) → ESP gửi `VOICE_EVENT` (và PCM nếu `VOICE_MODE 2`) → PC chọn mức tương ứng và
gửi `START_FILL` xuống → bơm chạy.

* Mic **analog (MAX9814)**: chỉnh `VAD_ON_RMS`/`VAD_OFF_RMS` nếu nhận quá nhạy (nhiễu quạt bơm) hoặc
  quá kém nhạy (nói to vẫn không thấy). Xem log trên Serial Monitor: firmware in `Mic: N tieng, rms ...`.
* Mic **I2S (INMP441)**: nối SCK=14, WS=15, SD=33, VDD=3V3, L/R→GND (kênh trái), đặt `MIC_TYPE 2`.

---

## 7) Kiểm tra trước khi chạy thật (không cần camera)

```bash
# 0) trên PC, cài phụ thuộc
pip install -r requirements_pc.txt        # đã gồm pyserial + numpy + opencv

# 1) thử toàn bộ luồng với ESP32 GIẢ LẬP (không cần phần cứng)
python3 tools/esp_cli.py --port sim --demo

# 2) cắm ESP32 thật, tìm cổng COM (Windows: Device Manager ▸ Ports)
python3 tools/esp_cli.py --port COM5      # rồi gõ 'help' để xem lệnh
python3 tools/esp_cli.py --port /dev/ttyUSB0     # Linux/macOS
```

Trong `esp_cli.py` có thể thử từng bước đúng như thao tác thật:

```
> status                 # xem trạng thái ESP (LED, cờ, ml)
> cup                    # (chỉ với --sim) đặt cốc giả
> ok 300                 # gửi CUP_OK: mở khoá nút/mic, cho rót tối đa 300 ml
> press 3                # (chỉ với --sim) bấm nút thứ 3 = 200 ml -> bơm chạy
> watch                  # in liên tục mọi gói tin ESP gửi lên
> tare                   # đo lại mặt khay khi khay TRỐNG
```

Với ESP thật, thay `cup`/`press` bằng thao tác tay: **đặt cốc** → LED nháy nhanh → `ok 300` →
LED sáng đều → **bấm nút** → bơm chạy.

---

## 7.5) Xem trên web / điện thoại (khuyến nghị khi lắp máy thật)

```bash
python3 tools/web.py --port COM5          # rồi mở http://<ip-may>:8080
python3 tools/web.py --port sim           # xem thử giao diện, bấm "Đặt cốc" trên web
```

Trang web hiện: ảnh camera (chỉ khi có cốc), trạng thái bo ESP32 (BOOT/IDLE/WAIT_PC/READY/
POURING/DONE/FAULT/MANUAL), 🔒/🔓 nút đang khoá hay đã mở, số ml đã rót / mục tiêu, nhật ký
gần nhất, cùng các nút thao tác tương đương phần cứng: **đặt cốc / nhấc cốc / bấm nút 1-5 /
nói 1-5 tiếng / gửi CUP_OK / rót / dừng / tare / self-test** (các nút mô phỏng chỉ hiện khi
chạy `--port sim`).

## 8) Chạy cả hệ thống (ESP32 + camera + nhận diện)

```bash
python3 tools/run_pc.py --port COM5            # ESP thật + camera + nhận diện tự động
python3 tools/run_pc.py --port sim --synthetic --demo  # chạy thật, 10 bước, in ✔/✘
```

Luồng tự động (`cupfiller/session.py`):

1. ESP báo `CUP_PLACED` → PC **mở camera** (trước đó camera tắt để tiết kiệm điện/nhiệt).
2. PC nhận diện cốc vài khung cho ổn định → đo bán kính/chiều cao → tính thể tích rót tối đa
   (có biên an toàn) → gửi `CUP_OK`.
3. ESP mở khoá nút/mic → người dùng bấm nút (hoặc nói) → ESP gửi `PRESET_SELECTED`.
4. ESP rót theo ml/s đã hiệu chuẩn (chế độ mặc định) **hoặc** PC điều khiển vòng kín bằng vạch nước
   (`SET_MODE` + `PUMP_SET`, khi bật `esp.pc_controls_pump`).
5. Nhấc cốc → ESP tự ngắt bơm ngay, báo `CUP_REMOVED` → PC đóng camera sau vài chục giây rảnh.

---

## 9) An toàn — những gì ESP32 TỰ LÀM, không trông chờ PC

| Tình huống | Phản ứng của ESP32 |
|---|---|
| Rót quá `MAX_FILL_ML` | ngắt relay, gửi `FILL_DONE` mã `OVER_VOLUME` |
| Rót quá `POUR_TIMEOUT_MS` | ngắt relay, mã `TIMEOUT` |
| Nhấc cốc giữa chừng | ngắt relay ngay, mã `CUP_REMOVED` |
| Cảm biến siêu âm hụt `CUP_FAULT_SAMPLES` mẫu liên tiếp (tuột dây/hỏng) | ngắt relay, mã `SENSOR_FAULT`, gửi `ERROR` + vào trạng thái LỖI |
| Mất UART với PC quá `LINK_TIMEOUT_MS` | ngắt relay, mã `LINK_LOST` |
| Giữ nút ≥ 2 s | DỪNG KHẨN CẤP (ngắt ngay khi đang giữ) |
| PC không trả lời `CUP_OK` sau `WAIT_PC_TIMEOUT_MS` | gửi `ERROR` `PC_khong_tra_loi_CUP_OK`; nút/mic **vẫn khoá** (không rót nước khi chưa xác nhận cốc). Gửi `CMD: RESET_STATE` để xoá lỗi |

Relay **tích cực mức CAO** (`RELAY_ACTIVE_HIGH 1`) đúng như yêu cầu thiết kế; nút bấm **tích cực
mức THẤP** với `INPUT_PULLUP` — nhấn là chạm GND, không cần điện trở ngoài.

---

## 10) Kiểm chứng không cần phần cứng

Bộ kiểm tra trên PC đối chiếu **từng byte** giữa C++ trong firmware và Python trên PC, chạy luôn
các kịch bản (khoá nút → CUP_OK → bơm → nhấc cốc → ngắt bơm):

```bash
python3 tools/test_comms.py                 # toàn bộ (183 bài)
python3 tools/test_comms.py --only firmware # chỉ phần config/sketch
python3 tools/test_comms.py --only fwrun    # CHẠY THẬT firmware trên PC (máy ảo mini)
```

`firmware/host_test/test_protocol.cpp` biên dịch được bằng g++ thường (không cần ESP32) và so khớp
với `firmware/host_test/protocol_vectors.txt` (45 vector vàng, sinh bằng
`tools/gen_protocol_vectors.py`). Sửa giao thức thì **sinh lại vector rồi chạy lại bộ test**.

`firmware/host_test/test_firmware_run.cpp` còn đi xa hơn: nó `#include` **chính file
`esp32_cup_filler.ino`** rồi chạy trên một máy ảo mini (`arduino_stub.h`: đồng hồ ảo, chân GPIO
thật, HC-SR04 giả, mic analog giả, UART2 nối vào PC giả). Nhờ vậy kiểm chứng được hành vi thật
của firmware mà không cần cắm mạch: chưa có `CUP_OK` thì bấm nút **không** bơm, `CUP_OK` mới mở
khoá, relay **tích cực mức CAO**, rót đúng số ml, và nhấc cốc / mất liên lạc UART 3 s / quá trần
thể tích / quá thời gian / giữ nút 2 s đều **ngắt bơm ngay**. Các gói firmware phát ra được đưa
ngược vào `cupfiller/protocol.py` để giải mã — PCM của mic còn được chính `VoiceRecognizer` của
PC nhận dạng lại. Biên dịch tay:

```bash
g++ -std=c++11 -O2 -I serial_esp32/include -I firmware/esp32_cup_filler \
    -I firmware/host_test -I firmware/host_test/arduino_stub -o /tmp/fwrun \
    firmware/host_test/test_firmware_run.cpp firmware/host_test/arduino_stub/instances.cpp -lm
/tmp/fwrun          # in CHECK OK/FAIL, TX..., RESULT <đạt> <lỗi>
```
