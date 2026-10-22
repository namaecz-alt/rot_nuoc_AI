# pi_esp32 — PlatformIO cho bản ESP32 nối Raspberry Pi

Đây là thư mục project VS Code + PlatformIO của firmware mới. Mở đúng thư mục này,
chọn môi trường **`esp32dev_pi`**, rồi Build/Upload.

Mã nguồn thật nằm ở `../firmware/esp32_pi_link/`:

| Muốn sửa | File |
|---|---|
| GPIO, baud UART 961000, lưu lượng bơm | `../firmware/esp32_pi_link/pi_config.h` |
| UART2 ESP32 <-> Pi | `../firmware/esp32_pi_link/pi_link.h` |
| Thứ tự `[cảm biến][START][mức1][mức2][mức3]` | `../firmware/esp32_pi_link/pi_protocol.h` |
| Mic INMP441 + loa MAX98357A | `../firmware/esp32_pi_link/audio_duplex.h` |
| LCD 2004 I2C GPIO21/22 | `../firmware/esp32_pi_link/lcd_2004_i2c.h` |
| Máy trạng thái rót | `../firmware/esp32_pi_link/esp32_pi_link.ino` |

Đọc tài liệu đầy đủ tại `../firmware/esp32_pi_link/README.md`.
