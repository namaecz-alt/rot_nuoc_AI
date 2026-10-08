// =====================================================================
//  GIAO THUC UART giua ESP32 (may rot) va MAY TINH
//  ---------------------------------------------------------------------
//  Day la file CHUAN (canonical) cua giao thuc. Ban Python nam o
//  cupfiller/serial_link.py va PHAI trung khop 100%.
//
//  Khuung tin dang van ban (doc duoc tren Serial Monitor de go loi):
//
//      $TYPE[,arg1,arg2,...]*XX\n
//
//    $      ky tu bat dau khuung
//    TYPE   2..8 ky tu A-Z / 0-9 (ten ban tin)
//    ,      dau phan cach, co the co nhieu
//    arg    chuoi in duoc, KHONG chua '$' '*' '\n' '\r'
//    *      ky tu ket thuc phan than
//    XX     2 chu so hex (chu thuong) = checksum
//    \n     ket thuc (bo giai ma van chap nhan neu thieu)
//
//  CHECKSUM = tong cac byte NAM GIUA '$' va '*' (khong tinh 2 ky tu do)
//             roi lay modulo 256, viet 2 chu so hex chu thuong.
//
//  Vi du:  $CUP,1*4b\n
//          "CUP,1" -> 0x43+0x55+0x50+0x2C+0x31 = 0x145 -> & 0xFF = 0x45  ("45")
//
//  File nay KHONG phu thuoc Arduino -> bien dich va unit-test duoc tren PC.
// =====================================================================
#ifndef CUPFILLER_UART_PROTOCOL_H
#define CUPFILLER_UART_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

namespace cupfiller {

// Do dai toi da cua phan than khuung ("TYPE,args"), ke ca ky tu ket thuc.
static const size_t kMaxBody = 64;

// ---- ma hoa ---------------------------------------------------------
uint8_t frame_checksum(const char *body, size_t len);

// Ghi khuung vao buffer `out`. Tra ve so byte da ghi (0 neu tran buffer).
size_t encode_frame(char *out, size_t out_cap, const char *type, const char *args);

// Tien loi: encode + gui ra link (xem link_io.h). Tra ve so byte da gui.
size_t send_frame(const char *type, const char *args = "");

// ---- giai ma --------------------------------------------------------
// May trang thai an tung byte; bo qua rac (khong co '$'), tu dong dong bo
// lai khi gap '$' giua khuung, dem so loi checksum.
class FrameParser {
 public:
  FrameParser() { reset(); }

  void reset();

  // Nap 1 byte. Tra ve true khi co mot khuung HOP LE san sang doc.
  bool feed(uint8_t b);

  const char *type() const { return type_; }  // "" neu chua co
  const char *args() const { return args_; }  // "" neu khong co tham so
  uint32_t errors() const { return errors_; } // so khuung sai checksum/qua dai

 private:
  enum State { S_IDLE, S_BODY, S_CRC1, S_CRC2 };
  State st_;
  char body_[kMaxBody];
  size_t n_;
  uint8_t crc_;
  char type_[kMaxBody];
  char args_[kMaxBody];
  uint32_t errors_;
};

// ---- tach tham so ---------------------------------------------------
// args = "3,200,ON" -> get_arg(args, 0, buf, n) = "3"; index 1 = "200"...
// Tra ve false neu khong du tham so.
bool get_arg(const char *args, int index, char *out, size_t cap);
int get_arg_int(const char *args, int index, int def);

}  // namespace cupfiller

#endif  // CUPFILLER_UART_PROTOCOL_H
