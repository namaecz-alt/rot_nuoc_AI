THÊM ẢNH CỐC CỦA BẠN

1) Ảnh: đặt JPG/PNG vào user/images/ (hoặc chạy tools/run_pc.py và bấm t để chụp).
2) Nhãn YOLO class 0=cup: user/labels/ten_anh.txt; mỗi dòng:
   0 x_center y_center width height
   Tọa độ chuẩn hóa 0..1; vẽ hộp quanh toàn bộ cốc.
3) Dùng tools/label_cups.py để vẽ hộp, tools/autolabel.py để tạo nhãn nháp,
   sau đó tools/train_yolo.py để fine-tune.

Không cần gán nhãn mực nước: vạch nước đo bằng thuật toán OpenCV và backlight.
Không dùng mAP trên ảnh tổng hợp để khẳng định hiệu năng webcam thật.
