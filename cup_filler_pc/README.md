# 💧 cup_filler — Máy rót nước tự động theo mức đặt trước

Nhận diện **cốc trong suốt** đặt vào máy bằng thị giác máy tính (không cân, không cảm biến mực nước),
sau đó rót nước tới **mức thể tích chọn trước** (100 / 150 / 200 / 250 / 300 ml…) bằng bơm điều khiển PWM.
Chạy trên **Raspberry Pi 4 + Camera Module / webcam USB**, hoặc chạy giả lập trên máy tính để thử thuật toán.

```
Đặt cốc → cảm biến báo ESP32 → ESP32 gửi UART → máy tính mới BẬT camera
       → camera thấy cốc qua đèn nền → đo hình dạng cốc → báo ACK xuống ESP32
       → ESP32 cho phép bấm nút chọn mức / nói bằng mic
       → bơm thô theo tích phân lưu lượng → học ánh xạ ml→mm của CHÍNH cốc đó
       → vòng kín tinh chỉnh theo vạch nước → lắng → bù xung nhỏ → XONG
```

## ESP32 + máy tính: ai làm gì

Phần cứng điều khiển nằm trên **ESP32**, còn thị giác máy tính chạy trên
**máy tính**. Hai bên nói chuyện bằng **UART 115200** (3 dây: TX, RX, GND).

```
  CẢM BIẾN CÓC ─►┐                     ┌─► CAMERA (chỉ bật khi có cốc)
  NÚT MỨC 1..5  ─►│      ESP32         │
  NÚT DỪNG      ─►│  ◄══ UART 115200 ══►  MÁY TÍNH (nhận diện + FSM rót)
  MIC / giọng nói►│                     │
  RELAY BƠM ◄───┘  (tích cực CAO)     └─► lệnh PUMP 0..100% xuống relay
```

Trình tự **bắt buộc**, không bước nào được đi tắt:

| Bước | Ai | Việc |
|---|---|---|
| 1 | ESP32 | Cảm biến thấy cốc (lọc rung 150 ms) → gửi `$CUP,1` |
| 2 | Máy tính | Nhận được tin đó **mới bật camera** và chạy nhận diện |
| 3 | Máy tính | Thấy đúng cốc → `$ACK,1`; không thấy → `$ACK,0` |
| 4 | ESP32 | Chỉ khi nhận `ACK,1` mới sang **ARMED**: cho phép nút bấm / mic |
| 5 | ESP32 | Người dùng bấm nút mức (hoặc nói) → `$START,<ml>` |
| 6 | Máy tính | Chạy FSM rót, gửi `$PUMP,<duty>` xuống; ESP32 băm mềm ra relay |
| 7 | Cả hai | Xong → `$STATE,DONE`. Nhả cốc / mất liên lạc → **cắt bơm cả hai phía** |

Quy ước điện đã chốt: **nút bấm và cảm biến tích cực mức THẤP, đặt
`INPUT_PULLUP`** (một đầu nút nối GND, không cần trở ngoài); **relay bơm tích
cực mức CAO** và được ghi 0 ngay khi khởi động.

Chốt an toàn: chưa có `ACK,1` thì mọi yêu cầu rót bị từ chối; máy tính im lặng
quá 800 ms thì ESP32 tự cắt relay; nhả cốc giữa chừng thì cắt relay trước, báo
tin sau. Toàn bộ có kiểm tra đơn vị.

* Firmware, sơ đồ nối, bảng giao thức: [`firmware/esp32/README_ESP32.md`](firmware/esp32/README_ESP32.md)
* Nạp firmware: `cd firmware/esp32 && pio run -e esp32dev -t upload`
  (project PlatformIO chuẩn; cũng chạy được bằng Arduino IDE — xem README trên)
* Chạy thử không cần phần cứng: `make -C firmware/esp32 test` (88 kiểm tra),
  `python tools/test_esp.py`, `python tools/run_esp.py --sim --synthetic --auto-test`

## Kết quả tự kiểm chứng (không cần phần cứng, xem `report/`)

| Hạng mục | Kết quả |
|---|---|
| Tìm thấy cốc (240 ảnh tổng hợp, nhiều cỡ/nhiễu/độ lệch camera) | **240/240** |
| Tìm thấy vạch nước khi cốc có nước | **232/234 (99.1%)**, 0–1 dương tính giả |
| Sai số vạch nước | **rms 1.8 mm**, max 12 mm (ca ngoại lệ) |
| Thời gian xử lý | **~6 ms/khung** (x86; Pi 4 ước tính 15–25 ms → đủ 15 fps) |
| Rót end-to-end 9 ca (3 preset × 3 dáng cốc) | **9/9 hoàn thành**, sai số **rms 3.8 ml, max 5.1 ml (≤ 5%)** |

Chạy lại: `python3 tools/test_pipeline.py` (hoặc `--quick`).

## Vì sao cốc trong suốt vẫn "nhìn thấy" được

Thuỷ tinh không có màu/texture riêng, nên bí quyết nằm ở **chiếu sáng ngược (backlight)**:
một dải đèn/tấm tán sáng đặt **phía sau** cốc. Khi đó camera thấy:

* thân cốc (không nước) = dải **tối vừa** trên nền sáng (thành cốc khúc xạ nền sáng lệch ra ngoài);
* phần cốc **chứa nước** = dải **tối đậm** (nước khúc xạ mạnh hơn nữa);
* **vạch nước** = biên ngang sắc nét giữa hai mức tối, kèm ellipse meniscus và hai "góc sáng" bám thành cốc;
* miệng cốc / đáy cốc = điểm đầu–cuối của dải tối.

Pipeline ảnh (thuần OpenCV cổ điển, không cần học sâu → chạy được trên Pi):

1. `find_strip` – dò dải nền sáng bằng percentile-80 theo cột (bền kể cả khi cốc che gần hết dải);
2. `find_cup` – tìm cột rồi tìm hàng của "bóng tối" → hộp cốc, miệng, đáy; nối dải cột bị dòng nước rót chém ngang;
3. `fit_walls` – hồi quy hai thành cốc → bán kính;
4. `find_waterline` – với mỗi cột, lấy biên dọc âm mạnh nhất có vùng **dưới biên tối mức nước**,
   rồi khớp **cung ellipse meniscus** `row = yc + ry·√(1−u²)` bằng RANSAC → `yc` là mặt phẳng nước tại trục cốc,
   **miễn nhiễm lệch thị giác** khi camera không đặt ngang mặt nước;
5. `WaterTracker` – trung vị thời gian + chặn bước nhảy phi vật lý (nước không tự tụt, không dâng nhanh hơn bơm).

## Kiến trúc điều khiển 3 lớp (bền với sai số hình học)

Đo khuôn cốc qua ảnh luôn sai vài % vì khúc xạ làm méo silhouette. Nên máy **không dựa vào khuôn cốc để định lượng**:

* **Lớp 1 – định lượng thô (mở vòng theo bơm):** tích phân lưu lượng hiệu chuẩn `V += flow·dt`;
  bơm giảm ga khi gần đích; không phụ thuộc chất lượng ảnh lúc rót mạnh (bọt, sóng, dòng nước).
* **Lớp 2 – học ánh xạ ml→mm của chính cốc đó:** trong lúc rót, ghi các cặp `(V đã bơm, mực nước đo được)`,
  hồi quy `h = f(V)` → đích `target_mm = f(preset)`. Phép đo này tự thích ứng cốc méo, gân, đáy lõm.
* **Lớp 3 – vòng kín thị giác:** pha FINE bơm PWM nhỏ tới `target_mm − lead` (lead bù nước còn trong bơm/ống + trễ đo),
  pha SETTLING chờ mặt nước phẳng rồi đọc lại, thiếu thì bù xung TOPUP ≤ 2 ml; thừa thì báo `OVERFILL`.

An toàn: timeout 45 s, mất cốc giữa chừng → ngắt bơm, giới hạn thể tích tuyệt đối, chặn đích sát miệng cốc,
cốc không đủ chứa → báo `CUP_TOO_SMALL` thay vì tràn.

## Chạy trên máy tính của bạn trước (Windows / macOS / Linux)

Đã đóng gói sẵn model YOLO COCO có lớp **cup** (`weights/yolo11n_coco.pt`) để thử webcam ngay, không phải train trước. Trên Windows có thể nhấp đúp `install_windows.bat`, sau đó `run_windows.bat`. Hướng dẫn chi tiết: [`README_PC.md`](README_PC.md).

```bash
pip install -r requirements_pc.txt
python tools/detect_pc.py --camera 0       # webcam (q thoát)
python tools/detect_pc.py --camera 1       # webcam thứ hai
python tools/detect_pc.py --image cup.jpg --save out.jpg
python tools/detect_pc.py --source cup.mp4 # file video
```

Thêm ảnh thật sau: chép ảnh vào `datasets/cups/user/images/`, chạy `python tools/label_cups.py` để vẽ hộp cốc (hoặc `python tools/autolabel.py` làm nhãn nháp), rồi `python tools/train_yolo.py --epochs 30`. Model cá nhân được lưu vào `weights/cup_yolo.pt`. YOLO COCO là model cốc nói chung, **không đảm bảo nhận ra mọi cốc trong suốt**; backlight và ảnh cốc thực tế vẫn quan trọng.

```bash
python tools/run_pc.py --camera 0         # UI FSM dry-run, không bật bơm thật
python tools/run_pc.py --video cup.mp4
python tools/run_pc.py --synthetic
```

`run_pc.py` hiển thị detector + mực nước (cần backlight), nhưng thao tác bơm trên PC chỉ là mô phỏng. **Không điều khiển bơm thật từ PC.**

## Cấu trúc thư mục

```
cup_filler/
├── cupfiller/                 # thư viện lõi (detection/controller/camera/pump/webapp)
│   ├── detection_yolo.py      # YOLO tìm cốc + CV đo vạch nước
│   ├── yolo_dataset.py        # sinh dataset có nhãn tổng hợp
│   ├── serial_link.py         # giao thức UART với ESP32 (bản Python)
│   ├── host_app.py            # điều phối phía PC: chờ CUP,1 -> bật camera -> ACK -> rót
│   └── esp_sim.py             # "ESP32 ảo" bằng Python (chạy thử không cần nạp)
├── firmware/esp32/            # FIRMWARE ESP32 - project PlatformIO (src/, test/, host/)
├── weights/yolo11n_coco.pt    # pretrained COCO (cup), chạy ngay
├── weights/cup_yolo.pt        # model của bạn sau khi train
├── tools/
│   ├── detect_pc.py           # webcam / ảnh / video
│   ├── run_pc.py              # UI demo, FSM dry-run
│   ├── label_cups.py          # gán nhãn hộp cốc thủ công
│   ├── autolabel.py           # tạo nhãn nháp
│   ├── train_yolo.py          # fine-tune model của bạn
│   ├── test_pipeline.py       # tự kiểm chứng CV/controller
│   ├── live.py                # live view Pi
│   ├── calibrate.py           # hiệu chuẩn Pi
│   ├── web.py                 # giao diện web
│   └── build_report.py        # dựng báo cáo .docx
├── datasets/cups/user/        # thêm ảnh và nhãn của bạn ở đây
├── web/simulation.html        # mô phỏng tương tác
└── report/                    # kết quả test + tài liệu thiết kế
```

## Chạy kiểm thử tổng hợp / giao diện

```bash
pip install -r requirements.txt
python3 tools/test_pipeline.py           # số liệu kiểm chứng + biểu đồ
python3 tools/live.py --synthetic        # cửa sổ live giả lập
python3 tools/web.py --synthetic        # web UI tại http://localhost:8080
```
Mở `web/simulation.html` trong trình duyệt để xem mô phỏng trực quan.

## Lắp phần cứng (Pi 4)

```
                 ┌──────────────┐
   dải đèn nền ──►│  CỐC TRONG   │◄── vòi rót ← bơm 12V ← MOSFET/relay ← BCM18 (PWM)
   (sau cốc)     └──────┬───────┘
                        │
              camera USB/CSI nhìn ngang thân cốc, cách ~35–45 cm
```

* **Đèn nền:** tấm tán sáng hoặc dải LED trắng đặt sau cốc, sáng đều, rộng hơn cốc ~1.5 lần.
  Đây là chi tiết **quan trọng nhất** — không có nó cốc thuỷ tinh gần như vô hình.
* **Camera:** đặt ngang khoảng giữa thân cốc, trục quang học càng gần mặt nước đích càng tốt
  (giảm lệch thị giác; phần mềm đã bù bằng khớp ellipse meniscus nên lệch vẫn chạy được).
* **Bơm:** bơm màng/bơm chìm 12 V qua MOSFET (khuyên dùng) hoặc relay module; băm PWM 200 Hz chân BCM18.
  Tránh relay on/off thô ở pha FINE — cần PWM để rót rỉ cuối kỳ.
* **Khay hứng + gá cốc:** cố định vùng đặt cốc để ROI ổn định; khay có rãnh thoát nước tràn.

```bash
sudo apt install python3-opencv python3-picamera2
pip install flask rpi.gpio
cp config/settings.example.yaml config/settings.yaml   # sửa camera.backend: picamera2|v4l2, pump.type: gpio
python3 tools/calibrate.py          # wizard 4 bước: scale mm/px → strip → đường cong bơm → bù meniscus
python3 tools/web.py                # UI điều khiển từ điện thoại cùng mạng
```

## Hiệu chuẩn nhanh (4 bước, `tools/calibrate.py`)

1. **Scale mm/px:** dán thước giấy sau cốc, bấm 2 vạch cách nhau 50 mm trên ảnh.
2. **Strip:** xác nhận khung xanh ôm đúng dải đèn nền.
3. **Đường cong bơm:** bơm vào cốc đong ở PWM 0.5 / 0.75 / 1.0 trong N giây, nhập thể tích đọc được → ml/s.
4. **Bù meniscus/parallax:** rót tới vạch 200 ml trên cốc đong, máy tự so số đo vạch nước → offset mm.

Kết quả ghi vào `config/calibration.json`, tự nạp đè lên `settings.yaml`.

## Thông số nên biết trong `settings.yaml`

| Khoá | Ý nghĩa |
|---|---|
| `calibration.mm_per_px` | tỉ lệ ảnh–thật, đo ở bước 1 |
| `calibration.in_flight_ml` | lượng nước còn bay trong không khí khi bơm ngắt |
| `control.pump.flow_curve` | đường cong lưu lượng hiệu chuẩn |
| `control.loop.settle_s` | thời gian chờ sóng/bọt lắng trước khi đọc kết quả |
| `control.loop.max_overfill_mm` | ngưỡng báo OVERFILL (đừng đặt < 4 mm: nhiễu đo ~±2 mm) |
| `safety.max_volume_ml` | chặn tuyệt đối, độc lập với mọi thuật toán |

## Giới hạn đã biết & hướng nâng cấp

* Sai số thể tích hiện ~±2–5 % đến từ đường cong bơm + nhiễu vạch nước; **muốn ±1 ml** thì thêm
  **load cell HX711** dưới khay (cân vòng kín bù drift lưu lượng) — kiến trúc 3 lớp vẫn giữ nguyên, chỉ thay lớp 1.
* Cốc có **hoa văn chìm / màu khói đậm** che vạch nước: tăng `waterline.grad_threshold` hoặc chuyển sang
  YOLOv8-n segment cốc+nước (Pi 4 chạy ~10 fps với engine TensorRT) rồi giữ nguyên lớp điều khiển.
* Bọt dày (nước có ga): tăng `settle_s`, hoặc đo bằng sóng siêu âm JSN-SR04T từ trên miệng.
* Nhiều cốc cùng lúc / băng tải: thêm bước theo dõi ROI per-cup — nằm ngoài phạm vi máy rót đơn này.
