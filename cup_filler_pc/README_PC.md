# Chạy chương trình nhận diện cốc trên máy tính Windows

Bộ này để thử **nhận diện cốc bằng webcam** trên máy tính thường trước khi lắp Raspberry Pi. Không cần có cốc train mới chạy được: model YOLO COCO có sẵn lớp `cup`, trọng số được đóng gói trong `weights/yolo11n_coco.pt`.

> **Giới hạn quan trọng:** model pretrained nhận diện cốc uống nước nói chung, không được bảo đảm nhận ra mọi cốc thủy tinh trong suốt. Với cốc trong, nên đặt một tấm/dải đèn sáng phía sau cốc. Muốn model hợp với cốc và góc camera của bạn, hãy chụp rồi gán nhãn ảnh của chính máy, sau đó train theo các bước bên dưới. YOLO chỉ tìm hộp cốc; đo vạch nước là một thuật toán OpenCV riêng và cần backlight.

## 1) Cài đặt trên Windows

1. Cài Python 3.10–3.13 từ [python.org](https://www.python.org/downloads/windows/), chọn **Add Python to PATH**.
2. Giải nén toàn bộ thư mục dự án, không đổi tên/tháo rời thư mục `weights`.
3. Nhấp đúp `install_windows.bat`. Lần cài đầu có thể mất vài phút và cần Internet vì PyTorch/Ultralytics khá lớn.

Hoặc mở Terminal/CMD trong thư mục dự án:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements_pc.txt
```

## 2) Mở webcam — thử ngay

Nhấp đúp **`run_windows.bat`** để chạy detector OpenCV, khuyến nghị cho cốc trong suốt với dải đèn nền phía sau. Hoặc:

```bat
.venv\Scripts\python.exe tools\run_pc.py --camera 0
```

Phím `q` để thoát. Nếu máy có nhiều camera, thử `--camera 1`. Detector cổ điển cần backlight để thấy thành cốc và vạch nước. Muốn so sánh bộ tìm cốc YOLO, chạy `tools\run_pc.py --camera 0 --yolo`; phím `d` chỉ ẩn/hiện hộp vẽ, không đổi detector.

Bạn sẽ thấy trạng thái nhận diện, vạch nước nếu có backlight, và vùng bơm là **dry-run**. Để thử YOLO COCO nhận diện cốc thông thường trên ảnh/video/webcam (cốc trong suốt có thể bị bỏ sót), chạy:

```bat
.venv\Scripts\python.exe tools\detect_pc.py --camera 0
.venv\Scripts\python.exe tools\detect_pc.py --image anh_coc.jpg --save ket_qua.jpg
.venv\Scripts\python.exe tools\detect_pc.py --source video.mp4
```

## 3) Chụp ảnh của chính bạn để train sau

Có thể chụp ảnh trước bằng webcam. Chạy bộ điều khiển xem trước (dry-run, không điều khiển GPIO/bơm thật):

```bat
.venv\Scripts\python.exe tools\run_pc.py --camera 0
```

Trong cửa sổ video:

- `t`: lưu khung hình vào `datasets/cups/user/images/`;
- `q`: thoát.

Hoặc tự chép ảnh JPG/PNG vào `datasets/cups/user/images/`. Nên có khoảng 50–200 ảnh khác nhau: cốc rỗng/có nước, các mức nước, góc nhìn và ánh sáng thực tế. Giữ đúng vị trí camera/backlight dự định dùng.

## 4) Gán nhãn hộp cốc (mỗi ảnh chỉ cần một hộp)

Chạy công cụ gán nhãn có sẵn, không cần cài labelImg:

```bat
.venv\Scripts\python.exe tools\label_cups.py
```

Với mỗi ảnh, dùng chuột **kéo hình chữ nhật bao trọn cái cốc**, rồi nhấn Enter/Space để lưu. Nếu ảnh không có cốc: nhấn `n`; bỏ qua ảnh: nhấn `s`; thoát: nhấn `q`. Nhãn YOLO được tạo tại `datasets/cups/user/labels/`, lớp duy nhất là `cup`.

Có thể thử nhãn tự động trước:

```bat
.venv\Scripts\python.exe tools\autolabel.py
```

Sau đó kiểm tra/sửa nhãn bằng `label_cups.py --force` (để ghi đè nhãn nháp). YOLO COCO có thể bỏ sót cốc trong suốt; ảnh bị bỏ sót cần vẽ hộp thủ công.

## 5) Train model riêng của bạn

```bat
.venv\Scripts\python.exe tools\train_yolo.py --epochs 30 --imgsz 480 --batch 4 --device cpu
```

- Nếu chưa có dữ liệu, chương trình tự sinh một bộ **240 ảnh tổng hợp train + 60 validation**, có nhãn hộp cốc tự động. Đây là kiểm tra pipeline, **không thay thế ảnh thật**.
- Tôi đã chạy thử pipeline 8 epoch trên tập tổng hợp: mAP50 **0.995**, mAP50–95 **0.948**; kiểm tra detector trên thêm 30 ảnh tổng hợp thấy cốc 30/30. Đây **không phải** độ chính xác trên ảnh webcam thật (chi tiết ở `report/yolo_train_synthetic.json`).
- Ảnh/nhãn bạn tự thêm sẽ được trộn vào tập train.
- Kết quả lưu thành `weights/cup_yolo.pt`; dùng `tools/detect_pc.py` hoặc `tools/run_pc.py --yolo` để chạy model vừa train. Detector OpenCV cổ điển vẫn là mặc định khuyến nghị cho cốc trong + backlight.
- Khi mới train mà chỉ có ảnh tổng hợp, chỉ số validation phản ánh ảnh tổng hợp, không chứng minh độ chính xác trên webcam thật. Thêm ảnh thật đã gán nhãn rồi train lại.

Có GPU NVIDIA CUDA? Thay `--device cpu` bằng `--device 0`. Nếu PC ít RAM, giảm `--imgsz 320` hoặc `--batch 2`.

## 6) Chạy bộ demo đầy đủ (dry-run)

```bat
.venv\Scripts\python.exe tools\run_pc.py --camera 0
```

`1..5` chọn preset 100/150/200/250/300 ml; `s` chạy FSM mô phỏng; `e` dừng; `t` chụp ảnh; `q` thoát. `--yolo` bật YOLO làm bộ tìm cốc; mặc định là detector OpenCV cho cốc trong có backlight. Chương trình trên PC chỉ **mô phỏng lệnh bơm**, không có phần cứng bơm; tuyệt đối không đấu bơm trực tiếp vào máy tính. Khi lắp phần cứng Raspberry Pi, hiệu chuẩn lại scale mm/px và lưu lượng bơm trước khi rót thật.

## 7) Cấu trúc file cần giữ

```text
cup_filler/
├── weights/yolo11n_coco.pt        # model COCO có sẵn lớp cup, chạy ngay
├── weights/cup_yolo.pt            # xuất hiện sau khi bạn train model riêng
├── cupfiller/                     # mã detector/controller/camera/pump
├── tools/detect_pc.py             # nhận diện webcam/ảnh/video
├── tools/run_pc.py                # UI demo + dry-run
├── tools/label_cups.py            # gán nhãn thủ công
├── tools/autolabel.py             # nhãn nháp tự động
├── tools/train_yolo.py            # train riêng
└── datasets/cups/user/{images,labels}/
```

## Dataset/model tìm được

- YOLO COCO đã pretrained có lớp `cup`, nên có thể dùng ngay để thử nhận diện đồ uống thông thường. Lớp COCO là tổng quát; không chỉ chuyên cốc thủy tinh trong và không cung cấp mặt nước. Danh sách lớp COCO gồm `cup`: [tham khảo danh sách COCO](https://blog.roboflow.com/microsoft-coco-classes/).
- Có dataset cộng đồng “Cup Detection” 201 ảnh, giấy phép CC BY 4.0 trên Roboflow Universe: [xem dataset](https://universe.roboflow.com/test-oyysq/cup-detection-ne45p). Dataset này chủ yếu là hộp cốc nói chung; **không nên giả định** nó có cốc trong suốt/backlight hay nhãn vạch nước. Vì vậy project cung cấp trọng số COCO sẵn + công cụ để bạn gán nhãn chính ảnh máy của mình, cách này phù hợp hơn để nhận diện cốc cụ thể.

## Nếu webcam báo lỗi MSMF / `can't grab frame` trên Windows

Bản cập nhật thử DirectShow trước rồi mới fallback Media Foundation, đồng thời kiểm tra lấy được frame trước khi mở giao diện. Nếu còn lỗi:
1. Đóng ứng dụng Camera, Teams, Zoom, trình duyệt hoặc ứng dụng nào đang dùng webcam.
2. Kiểm tra Windows Settings → Privacy & security → Camera, bật quyền cho desktop apps.
3. Thử `--camera 1` (hoặc số camera khác) và chọn 640×480 nếu webcam hỗ trợ.
4. Rút/cắm lại webcam rồi khởi động lại chương trình.

Nếu muốn giữ nguyên thư mục dữ liệu/ảnh đã chụp, giải nén bản cập nhật đè lên thư mục code hiện tại; không xóa `datasets/cups/user/images/` hoặc `datasets/cups/user/labels/`.

---

## 3) Dùng kèm bo ESP32 (nút bấm + cảm biến cốc + bơm)

Bo ESP32 lo phần cứng (cảm biến cốc, 5 nút chọn mức, mic, relay bơm), máy tính lo nhận diện cốc.
Hai bên nói chuyện qua cáp UART (USB-TTL). Chi tiết dây và cách nạp: **`firmware/README.md`**;
đặc tả gói tin: **`docs/PROTOCOL.md`**.

**Thử trước khi có phần cứng** — nhấp đúp **`esp_sim_windows.bat`**: chương trình dùng ESP32
*giả lập* và chạy đúng kịch bản thật (đặt cốc → xác nhận → bấm nút → rót 200 ml).

**Khi đã có bo ESP32:**

1. Nạp firmware `firmware/esp32_cup_filler/esp32_cup_filler.ino` bằng Arduino IDE
   (Board: **ESP32 Dev Module**), nhớ nối dây UART2 → mạch USB-TTL → cổng USB của máy tính.
2. Nhấp đúp **`esp_test_windows.bat`** và nhập cổng COM (ví dụ `COM5`) để kiểm tra nhanh:
   đặt cốc → bấm nút → bơm chạy → nhấc cốc → bơm dừng.
3. Chạy cả hệ thống: **`run_esp_windows.bat`** (nhập cổng COM) hoặc
   `.venv\Scripts\python.exe tools\run_pc.py --port COM5`.

Từ đây camera **chỉ bật khi bạn đặt cốc vào khay**; nút bấm trên ESP32 chỉ hoạt động sau khi
máy tính đã nhận diện xong cốc (đèn LED trên ESP32 sáng đều = đã mở khoá).

Cần chỉnh thông số cho đúng máy của bạn (chân nút, relay, lưu lượng ml/s, ngưỡng mic…):
mở `firmware/esp32_cup_filler/config.h`, sửa rồi nạp lại. Trên máy tính có thể hiệu chuẩn nhanh
mà không cần nạp lại firmware:

```bat
.venv\Scripts\python.exe tools\esp_cli.py --port COM5
esp> params 41.0 500 60      # lưu lượng 41 ml/s, trần 500 ml, timeout 60 giây
esp> tare                    # đo lại mặt khay (nhấc cốc ra trước)
esp> watch                   # xem mọi gói tin ESP gửi lên
```

Muốn xem trên điện thoại/máy tính khác trong mạng LAN (ảnh camera + trạng thái bo + nút bấm):

```bat
.venv\Scripts\python.exe tools\web.py --port COM5
```

rồi mở `http://<địa-chỉ-máy>:8080`. Không có bo ESP32 vẫn xem thử được toàn bộ giao diện
bằng `--port sim` (bấm "Đặt cốc" ngay trên web).

**Tự kiểm chứng toàn bộ phần giao tiếp** (không cần ESP32, không cần camera):

```bat
.venv\Scripts\python.exe tools\test_comms.py
```

Kết quả mong đợi: `157/157 bài đạt` — kiểm tra khung gói + CRC, đối chiếu firmware C++ với
Python từng byte, đọc số tiếng Việt, luồng đặt cốc → CUP_OK → bấm nút → rót, giao diện web,
cả chương trình `run_pc.py` thật và cả **firmware được chạy thật trên PC** (nhóm `fwrun`:
nút còn khoá thì bấm không ăn, relay tích cực mức CAO, rót đúng ml, nhấc cốc/mất liên lạc/
quá thể tích/quá thời gian/dừng khẩn cấp/mất cảm biến đều ngắt bơm, chờ `CUP_OK` đúng hạn).
