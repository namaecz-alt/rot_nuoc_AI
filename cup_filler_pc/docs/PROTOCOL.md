# 📡 Giao thức UART giữa ESP32 và máy tính (protocol v1)

Tài liệu này là **hợp đồng** giữa hai phía:

| Phía | Mã nguồn | Kiểm chứng |
|---|---|---|
| MÁY TÍNH (PC) | `cupfiller/protocol.py` | `tools/test_comms.py` nhóm `[1]` |
| ESP32 (firmware) | `firmware/esp32_cup_filler/protocol.h` | `tools/test_comms.py` nhóm `[2]`, `[6]` |

Hai bản phải **giống nhau từng byte**. Bằng chứng là bộ **vector vàng**:
`firmware/host_test/protocol_vectors.txt` (44 vector, sinh bằng `tools/gen_protocol_vectors.py`)
— bản ghi trên dây để cả Python lẫn C++ đối chiếu.

---

## 1) Khung gói tin (frame)

```
┌──────┬──────┬─────┬─────┬─────┬───────────────┬───────┬───────┐
│ 0xAA │ 0x55 │ LEN │ SEQ │ MSG │   PAYLOAD…    │ CRC lo│ CRC hi│
└──────┴──────┴─────┴─────┴─────┴───────────────┴───────┴───────┘
   đồng bộ      │      │     │        LEN-2 byte
                │      │     └── mã gói tin (xem §2)
                │      └──────── số thứ tự 0..255 (mỗi bên tự tăng, để phát hiện mất gói)
                └────────────── LEN = 2 + số byte payload  (2..242)
```

* Tổng số byte của khung = `LEN + 5`.
* `CRC` = **CRC-16/CCITT-FALSE** (đa thức `0x1021`, khởi tạo `0xFFFF`, không đảo bit),
  tính trên `LEN`, `SEQ`, `MSG` và payload — tức `LEN + 1` byte kể từ vị trí `LEN`.
  Vector chuẩn: `CRC("123456789") = 0x29B1`.
* Byte thấp của CRC gửi trước (little-endian).
* Bộ giải mã **tự đồng bộ lại** sau nhiễu: gặp LEN vô lý hoặc CRC sai thì trượt 1 byte rồi dò
  lại `AA 55`, nên một gói hỏng không làm mất các gói đi sau (đã kiểm tra với rác, gói cụt, CRC hỏng).
* Thứ tự byte trong payload: **little-endian** (giống `struct` của Python và ESP32).
* Mọi trường `text` là UTF-8 **không có byte 0**, đệm bằng `\0` cho đủ độ dài.

## 2) Danh sách gói tin

### ESP32 → máy tính

| MSG | Tên | Payload | Ý nghĩa |
|---|---|---|---|
| `0x01` | `HELLO` | `<BBBBH>` version, caps, n_presets, 0, flow×10 + `n×<H>` presets | chào khi khởi động / nhắc lại 2 s một lần khi chưa được trả lời |
| `0x02` | `CUP_PLACED` | `<HHHBI>` distance_mm, baseline_mm, height_mm, flags, uptime_ms | **cảm biến thấy cốc vừa được đặt** → PC mới bật camera |
| `0x03` | `CUP_REMOVED` | `<BI>` reason, uptime_ms | cốc đã bị nhấc ra (ESP đã ngắt bơm nếu đang bơm) |
| `0x04` | `PRESET_SELECTED` | `<BHB>` index, ml, source | người dùng chọn mức bằng nút/mic |
| `0x05` | `FILL_STARTED` | `<HBBH>` target_ml, mode, source, max_ml | relay đã đóng, bắt đầu bơm |
| `0x06` | `FILL_PROGRESS` | `<HBHB>` poured_ml, pct, target_ml, state | tiến độ (mặc định 5 Hz) |
| `0x07` | `FILL_DONE` | `<HHBI>` poured_ml, target_ml, status, elapsed_ms | kết thúc lượt rót (kèm mã dừng) |
| `0x08` | `STATUS` | `<BBHHHI>` state, flags, poured_ml, target_ml, max_ml, uptime_ms | nhịp tim + trạng thái (2 Hz hoặc khi đổi trạng thái) |
| `0x09` | `VOICE_EVENT` | `<BBBBHH>` kind, index, confidence, n_peaks, rms, dur_ms | kết quả nghe trên ESP |
| `0x0A` | `AUDIO_CHUNK` | `<BBH>` seq8, flags, n_samples + `n×<h>` mẫu PCM | PCM 16-bit gửi lên PC (khi `VOICE_MODE 2`) |
| `0x0B` | `BUTTON_EVENT` | `<BHBB>` index, press_ms, event, state | có nút được nhấn (kể cả dừng khẩn cấp) |
| `0x0C` | `ERROR` | `<B>` code + text(48) | lỗi phía ESP |
| `0x0D` | `ACK` | `<BB>` acked_msg, status | ESP xác nhận đã nhận gói của PC |
| `0x0E` | `PONG` | `<IBB>` uptime_ms, state, flags | trả lời `PING` |
| `0x0F` | `LOG` | text (UTF-8 thô) | dòng log để hiện trên màn hình PC |

### Máy tính → ESP32

| MSG | Tên | Payload | Ý nghĩa |
|---|---|---|---|
| `0x81` | `HELLO_ACK` | `<BBB>` version, caps, n_presets + `n×<H>` presets | PC đã sẵn sàng, gửi lại danh sách preset |
| `0x82` | `CUP_OK` | `<HHHHBB>` rim_r×10, base_r×10, height×10, max_ml, confidence(0..100), flags + label(16) | **PC đã nhận diện xong cốc → ESP MỞ KHOÁ nút bấm + mic** |
| `0x83` | `CUP_REJECT` | `<B>` reason + text(48) | không nhận diện được → ESP về `WAIT_PC`, chờ đặt lại |
| `0x84` | `SET_PRESET` | `<BHB>` index, ml, source | PC đặt hộ mức nước |
| `0x85` | `START_FILL` | `<HBBH>` ml, mode, source, timeout_ms | PC ra lệnh bơm |
| `0x86` | `STOP_FILL` | `<B>` reason | dừng bơm ngay |
| `0x87` | `SET_MODE` | `<HH>` flags_on, flags_off | bật/tắt tính năng (bit 0 = PC điều khiển bơm) |
| `0x88` | `PUMP_SET` | `<BH>` duty_pct, duration_ms | PC điều khiển relay trực tiếp (vòng kín thị giác) |
| `0x89` | `SET_PARAMS` | `<HHHH>` flow×10, max_ml, timeout_s, pulse_ms (`0xFFFF` = giữ nguyên) | hiệu chuẩn |
| `0x8A` | `CMD` | `<BH>` cmd, arg | lệnh rời: arm/disarm/tare/reset/self-test |
| `0x8B` | `PING` | `<I>` uptime_ms (của PC) | hỏi thăm, ESP trả `PONG` |
| `0x8C` | `ACK_PC` | `<BB>` acked_msg, status | PC xác nhận gói của ESP |
| `0x8D` | `ERROR_PC` | `<B>` code + text(48) | PC báo lỗi cho ESP |

> **Vì sao `ACK` là `0x0D` mà `ACK_PC` là `0x8C`?** Bit cao (0x80) đánh dấu chiều **PC→ESP**;
> `ACK`/`ERROR` của hai chiều là hai gói khác nhau, không dùng chung mã.

## 3) Ý nghĩa các trường đặc biệt

### `STATUS.flags`

| Bit | Tên | Nghĩa |
|---|---|---|
| 0 | `FL_CUP_PRESENT` | cảm biến đang thấy cốc |
| 1 | `FL_PC_CONFIRMED` | PC đã gửi `CUP_OK` (nút/mic ĐÃ mở khoá) |
| 2 | `FL_PUMPING` | relay đang bật |
| 3 | `FL_VOICE_ACTIVE` | có mic và đang nghe |
| 4 | `FL_LINK_OK` | ESP còn nhận được gói từ PC |
| 5 | `FL_MANUAL` | chế độ dự phòng (mất PC nhưng vẫn cho bấm nút) |

### `STATUS.state` — máy trạng thái của ESP

```
BOOT(0) ──► IDLE(1) ──đặt cốc──► WAIT_PC(2) ──CUP_OK──► READY(3) ──chọn mức──► POURING(4)
                  ▲                   │ (CUP_REJECT / quá lâu)        │ đủ ml / dừng / nhấc cốc
                  └───────────────────┴───────────────────────────────┴──► DONE(5) / IDLE(1)
   FAULT(6): lỗi nghiêm trọng (chỉ khi ALLOW_MANUAL_WHEN_LINK_LOST = 0)
   MANUAL(7): mất PC nhưng vẫn cho bấm nút (khi bật chế độ dự phòng)
```

**Nút bấm và mic CHỈ có tác dụng khi** `state == READY` **và** `FL_CUP_PRESENT` **và**
`FL_PC_CONFIRMED` (hoặc `MANUAL`). Đây chính là yêu cầu "PC xác nhận cốc rồi mới cho chọn mức".

### `CUP_OK.flags`

| Bit | Nghĩa |
|---|---|
| 0 | `CUP_FLAG_ESTIMATED` | thể tích là ước lượng (không đo được hình học đầy đủ) |
| 1 | `CUP_FLAG_PARTIAL` | cốc đang có nước sẵn (lượng rót tối đa đã trừ phần nước này) |

### `AUDIO_CHUNK.flags`

| Bit | Nghĩa |
|---|---|
| 0 | `AU_START` | chunk đầu tiên của một đoạn nói |
| 1 | `AU_END` | chunk cuối cùng — PC chốt kết quả nhận dạng sau chunk này |
| 4..7 | tần số lấy mẫu thực tế của ESP: `flags >> 4` × 2000 Hz (0 = dùng mặc định 16 kHz) |

### `BUTTON_EVENT.event`

| Giá trị | Tên | Nghĩa |
|---|---|---|
| 0 | `BTN_SHORT` | nhấn ngắn (< 1500 ms): chọn mức **và** bật bơm (nếu đã mở khoá) |
| 1 | `BTN_LONG` | nhấn giữ 1,5–2 s: chỉ chọn mức, chưa bơm (bấm ngắn lần nữa để bơm) |
| 2 | `BTN_ESTOP` | giữ ≥ 2 s: **DỪNG KHẨN CẤP** (ESP ngắt relay ngay, không chờ PC) |

### `FILL_DONE.status` (mã dừng)

| Mã | Tên | Ai tạo ra |
|---|---|---|
| 0 | `NORMAL` | ESP đủ ml mục tiêu |
| 1 | `PC_REQUEST` | PC gửi `STOP_FILL` / `CMD: DISARM` |
| 2 | `CUP_REMOVED` | cảm biến mất cốc |
| 3 | `TIMEOUT` | quá `POUR_TIMEOUT_MS` |
| 4 | `LINK_LOST` | mất UART quá `LINK_TIMEOUT_MS` |
| 5 | `BUTTON_ESTOP` | người dùng giữ nút 2 s |
| 6 | `OVER_VOLUME` | vượt trần `MAX_FILL_ML` (an toàn) |
| 7 | `SENSOR_FAULT` | cảm biến siêu âm hụt `CUP_FAULT_SAMPLES` mẫu liên tiếp (tuột dây/hỏng) |

### `FILL_STARTED.mode` / `Source` / `SET_MODE`

* `mode`: `0` = ESP tự đong theo ml/s đã hiệu chuẩn (mặc định, không cần camera),
  `1` = PC điều khiển vòng kín bằng `PUMP_SET` (nhìn vạch nước).
* `source`: `0` nút bấm, `1` giọng nói, `2` PC, `3` tự động.
* `SET_MODE` bit: `0x01` `PC_CONTROLS_PUMP`, `0x02` `VOICE_STREAM`, `0x04` `LINK_STOP_PUMP`.

## 4) Trình tự bắt tay đầy đủ (đúng như firmware chạy)

```
ESP32                                        MÁY TÍNH
  │ boot: relay OFF ngay, đo baseline khay     │
  │ HELLO (presets, flow) ──────────────────►  │
  │ ◄──────────────────────────── HELLO_ACK    │  (PC sẵn sàng; ESP học preset/flow)
  │ STATUS (IDLE) ─────────────────────────►   │  (2 Hz nhịp tim)
  │                                            │
  │ [người dùng đặt cốc]                        │
  │ CUP_PLACED (chiều cao cốc) ─────────────►  │  ← ĐÂY PC MỚI BẬT CAMERA
  │                                            │  nhận diện 3–4 khung cho ổn định
  │ ◄─────────────────────── CUP_OK (hình học) │  ← mở khoá nút/mic
  │ STATUS (READY, FL_PC_CONFIRMED) ─────────► │  (đèn LED sáng đều)
  │                                            │
  │ [bấm nút / nói]                            │
  │ PRESET_SELECTED ────────────────────────►  │
  │ FILL_STARTED (mục tiêu, mode) ──────────►  │  relay ĐÓNG (mức CAO)
  │ FILL_PROGRESS (5 Hz) ───────────────────►  │
  │ FILL_DONE (đã rót, mã dừng) ────────────►  │
  │                                            │
  │ [nhấc cốc]  → ESP ngắt bơm ngay nếu đang bơm│
  │ CUP_REMOVED ────────────────────────────►  │  → PC đóng camera sau vài chục giây
  │ ◄───────────────────────────── PING (1 Hz) │  PC giữ nhịp tim
  │ PONG ──────────────────────────────────►   │
```

Chế độ **PC điều khiển bơm** (`SET_MODE(PC_CONTROLS_PUMP)`), dùng khi muốn rót chính xác theo
vạch nước nhìn thấy:

```
PC: SET_MODE(on=PC_CONTROLS_PUMP) ──────────►
PC: START_FILL(ml=200, mode=PC_CLOSED_LOOP) ─►
ESP: FILL_STARTED, relay ON, duty=100% ─────►
PC: PUMP_SET(duty=0.5) ─────────────────────►  ESP băm relay chậm (chu kỳ 2 s) theo duty
PC: PUMP_SET(duty=0.0) ─────────────────────►  ESP ngắt relay
PC: STOP_FILL(NORMAL) ──────────────────────►  ESP gửi FILL_DONE
```

## 5) Quy tắc an toàn (bắt buộc giữ khi sửa code)

1. **Không rót khi chưa xác nhận cốc**: `start_pour()` từ chối nếu chưa có `CUP_OK` (hoặc `MANUAL`).
2. **Mất cốc = ngắt bơm ngay** (không chờ PC).
3. **Mất UART = ngắt bơm** sau `LINK_TIMEOUT_MS` (mặc định 3 s).
4. **Trần cứng** `MAX_FILL_ML` và **trần thời gian** `POUR_TIMEOUT_MS` do ESP tự áp dụng.
5. **Dừng khẩn cấp** bằng cách giữ nút 2 s — luôn hoạt động, kể cả khi PC treo.
6. Relay luôn về **OFF** khi khởi động (`FORCE_OFF_ON_BOOT`).

## 6) Sửa giao thức thì làm gì?

```bash
# 1. sửa cupfiller/protocol.py  VÀ  firmware/esp32_cup_filler/protocol.h
# 2. cập nhật bảng vector (thêm/sửa kịch bản trong cupfiller/protocol_vectors.py)
python3 tools/gen_protocol_vectors.py            # ghi lại firmware/host_test/protocol_vectors.txt
# 3. chạy kiểm chứng hai phía
python3 tools/test_comms.py                      # phải 100 % PASS
```

Nếu chỉ sửa **`protocol.h`** mà quên `protocol.py` (hoặc ngược lại), bước 3 sẽ báo lỗi
`byte tren day: byte N = 0x…, file vector 0x…` — đó chính là chỗ hai bên đã lệch nhau.

---

## 7) Công cụ đi kèm (dùng để thử giao thức này)

| Công cụ | Việc dùng |
|---|---|
| `tools/esp_cli.py --port sim --demo` | chạy kịch bản chuẩn, in ✔/✘ từng bước, không cần phần cứng |
| `tools/esp_cli.py --port COM5` | bảng điều khiển thật: `status`, `ok 300`, `preset 3`, `start`, `stop`, `watch`, `tare`, `params`, `selftest`… |
| `tools/web.py --port sim\|COM5` | giao diện web/điện thoại: ảnh camera + trạng thái bo + nút bấm tương đương phần cứng |
| `tools/run_pc.py --port sim --synthetic --demo` | chạy THẬT chương trình của người dùng, 10 bước, in ✔/✘ |
| `tools/test_comms.py` | 151 bài tự kiểm chứng: khung/CRC, vector vàng với C++ từng byte, luồng, web, firmware |
| `firmware/host_test/test_firmware_run.cpp` | chạy THẬT sketch ESP32 trên PC (máy ảo mini): khoá nút, mở khoá bằng CUP_OK, relay mức CAO, các kiểu ngắt bơm; gói firmware phát ra được `protocol.py` giải mã lại |
| `tools/gen_protocol_vectors.py` | sinh lại `firmware/host_test/protocol_vectors.txt` sau khi sửa giao thức |

**Sửa giao thức thì bắt buộc chạy lại:** `python3 tools/gen_protocol_vectors.py` rồi
`python3 tools/test_comms.py` (cả Python và C++ phải cùng đạt mới được nạp firmware).
