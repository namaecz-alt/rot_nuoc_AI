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
| 1 | ESP32 DevKit (ESP32-WROOM-32) | bất kỳ board "ESP32 Dev Module" |
| 2 | Module relay 1 kênh **tích cực mức CAO** | nếu module là loại tích cực mức thấp, đổi `RELAY_ACTIVE_HIGH` thành `0` |
| 3 | Bơm nước mini DC (3–6 V hoặc 12 V tuỳ loại) | **nguồn riêng** cho bơm, không lấy từ ESP32 |
| 4 | 5 nút nhấn (loại 4 chân hoặc 2 chân) | một chân nối **GND**, chân kia vào GPIO |
| 5 | HC-SR04 (siêu âm) **hoặc** cảm biến hồng ngoại / công tắc hành trình | mặc định dùng HC-SR04 gắn **trên** khay |
| 6 | Mic: MAX9814 / MAX4466 (analog) **hoặc** INMP441 / ICS-43434 (I2S) | analog đơn giản hơn |
| 7 | Mạch USB-TTL (CH340/CP2102) | nối UART2 của ESP32 với máy tính; 3V3 logic, **nối chung GND** |
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
| HC-SR04 TRIG | **5** | |
| HC-SR04 ECHO | **18** | |
| Mic analog (MAX9814 OUT) | **35** | chỉ đọc (ADC1), mic điện áp ~1/2 VCC khi im lặng |
| Mic I2S SCK / WS / SD | **14 / 15 / 33** | chỉ dùng khi `MIC_TYPE 2` |
| UART2 RX | **16** | ← TX của mạch USB-TTL |
| UART2 TX | **17** | → RX của mạch USB-TTL (nhớ nối chéo RX↔TX) |
| LED báo trạng thái | **2** | LED có sẵn trên board devkit |

Sơ đồ nối UART với máy tính:

```
ESP32 GPIO16 (RX2) ◄──── TXD  mạch USB-TTL  ────► cổng USB của PC
ESP32 GPIO17 (TX2) ────► RXD
ESP32 GND ────────────── GND           (BẮT BUỘC nối chung GND)
```

LED báo trạng thái: nháy chậm = chờ đặt cốc · **nháy nhanh = chờ PC xác nhận cốc** ·
**sáng đều = đã mở khoá, bấm nút được** · nháy gấp = đang bơm · nháy rất nhanh = lỗi.

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

### Nạp bằng PlatformIO (nếu bạn thích dòng lệnh)

```bash
pip install platformio
cd firmware
pio project init --board esp32dev      # chỉ chạy lần đầu
pio run -t upload                       # nạp
pio device monitor -b 115200            # xem log
```

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
| Mất UART với PC quá `LINK_TIMEOUT_MS` | ngắt relay, mã `LINK_LOST` |
| Giữ nút ≥ 2 s | DỪNG KHẨN CẤP (ngắt ngay khi đang giữ) |
| PC không trả lời `CUP_OK` | báo lỗi; nút/mic **vẫn khoá** (không rót nước khi chưa xác nhận cốc) |

Relay **tích cực mức CAO** (`RELAY_ACTIVE_HIGH 1`) đúng như yêu cầu thiết kế; nút bấm **tích cực
mức THẤP** với `INPUT_PULLUP` — nhấn là chạm GND, không cần điện trở ngoài.

---

## 10) Kiểm chứng không cần phần cứng

Bộ kiểm tra trên PC đối chiếu **từng byte** giữa C++ trong firmware và Python trên PC, chạy luôn
các kịch bản (khoá nút → CUP_OK → bơm → nhấc cốc → ngắt bơm):

```bash
python3 tools/test_comms.py                 # toàn bộ (77 bài)
python3 tools/test_comms.py --only firmware # chỉ phần config/sketch
```

`firmware/host_test/test_protocol.cpp` biên dịch được bằng g++ thường (không cần ESP32) và so khớp
với `firmware/host_test/protocol_vectors.txt` (44 vector vàng, sinh bằng
`tools/gen_protocol_vectors.py`). Sửa giao thức thì **sinh lại vector rồi chạy lại bộ test**.
