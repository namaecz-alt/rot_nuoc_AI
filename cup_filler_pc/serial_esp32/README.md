# 🔌 serial_esp32 — project VS Code + PlatformIO cho firmware ESP32

Thư mục này là **một project PlatformIO hoàn chỉnh**: mở đúng thư mục `cup_filler_pc/serial_esp32`
bằng VS Code là PlatformIO nhận project ngay (không cần `pio project init`).

```
cup_filler_pc/serial_esp32/
├── platformio.ini      ← file PlatformIO (2 môi trường: esp32dev / esp32dev_usb)
├── include/
│   └── serial_link.h   ← CODE GIAO TIẾP SERIAL VỚI PC (file riêng, sửa ở đây)
├── README.md           ← file này
└── .vscode/            ← gợi ý extension + cấu hình VS Code
```

Logic của máy rót nước (cảm biến cốc, 5 nút bấm, mic, bơm/relay, an toàn) vẫn nằm ở
`cup_filler_pc/firmware/esp32_cup_filler/` — `platformio.ini` trỏ `src_dir` vào đó, nên
**biên dịch trong thư mục này là ra firmware đầy đủ**, không có bản sao nào bị lệch nhau.

## Dùng trong VS Code (4 bước)

1. Cài extension **PlatformIO IDE** (`Ctrl+Shift+X` → *PlatformIO IDE*).
2. `File ▸ Open Folder...` → chọn **`cup_filler_pc/serial_esp32`** (đúng thư mục có `platformio.ini`).
   Lần build đầu PlatformIO tự tải ESP32 platform + toolchain (vài trăm MB, chỉ một lần).
3. Ở **thanh trạng thái dưới cùng**, chọn môi trường:

   | Môi trường | Kênh nói chuyện với PC | Cần gì thêm |
   |---|---|---|
   | `esp32dev` (mặc định) | UART2 – GPIO16 (RX) / GPIO17 (TX), 921600 baud | mạch USB-TTL (CH340/CP2102/FT232) |
   | `esp32dev_usb` | **cáp USB có sẵn của board** (Serial.write), 921600 baud | **không cần gì** |

4. Bấm **✓ Build** rồi **→ Upload**. Xem log bằng biểu tượng **phích cắm** (Serial Monitor):
   `esp32dev` 115200 · `esp32dev_usb` 921600 và **đừng mở khi đang chạy phần mềm PC**.

Lệnh tương đương trong terminal VS Code:

```bash
pio run                                  # biên dịch (env esp32dev)
pio run -t upload                        # nạp
pio device monitor                       # xem log tiếng Việt (115200)
pio run -e esp32dev_usb -t upload        # nạp bản dùng cáp USB của board
pio run -e esp32dev_usb -t monitor       # (bản này là kênh dữ liệu 921600, không phải log)
pio run -t erase                         # xoá flash khi board "cứng đầu"
```

## Phía máy tính (PC)

Không đổi gì so với trước — chỉ cần biết cổng COM của board (Windows: Device Manager ▸ Ports):

```bash
python3 tools/web.py --port COM5         # web UI chế độ ESP32
python3 tools/run_pc.py --port COM5      # chạy cả hệ thống
```

## Sửa code ở đâu?

| Muốn sửa | File |
|---|---|
| Cách gửi/nhận gói tin với PC (chọn cổng, đóng khung, gửi, nhận) | **`cup_filler_pc/serial_esp32/include/serial_link.h`** |
| Chân GPIO, tốc độ bơm, preset ml, chọn kênh UART2 hay cáp USB | `cup_filler_pc/firmware/esp32_cup_filler/config.h` |
| Logic máy rót (cảm biến, nút, mic, bơm, an toàn) | `cup_filler_pc/firmware/esp32_cup_filler/esp32_cup_filler.ino` |
| Định dạng từng byte của giao thức | `cup_filler_pc/firmware/esp32_cup_filler/protocol.h` |

## Lỗi hay gặp

* **`TypeError: ParamType.get_...var() missing 1 required positional argument: 'ctx'` khi build** →
  `esptool` trong `%USERPROFILE%\.platformio\penv` cần `click < 8.2`:
  `C:\pio\penv\Scripts\python.exe -m pip install "click<8.2"` rồi xoá thư mục `.pio` và build lại.
* **`Failed to connect` khi Upload** → giữ nút **BOOT** trên board khi nó hiện `Connecting...`;
  hoặc hạ `upload_speed` trong `platformio.ini` từ `921600` xuống `460800`.
* **Đừng xoá `build_src_filter`** trong `platformio.ini` — nó giữ cho mỗi môi trường chỉ biên dịch
  **một** bản firmware (`esp32_cup_filler.ino` *hoặc* `esp32_cup_filler_usb.cpp`), nếu không sẽ có
  hai `setup()`/`loop()`.

Xem thêm: `cup_filler_pc/firmware/README.md` (§2b các file, §3 nạp firmware, §3b dùng cáp USB).
