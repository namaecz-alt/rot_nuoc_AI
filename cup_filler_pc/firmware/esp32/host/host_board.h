// =====================================================================
//  MO PHONG PHAN CUNG cho firmware - bien dich tren PC bang g++
//  Dung de: (1) unit test logic ESP32, (2) chay "ESP32 ao" noi voi
//  chuong trinh that tren may tinh qua stdin/stdout (host_main.cpp).
// =====================================================================
#ifndef CUPFILLER_HOST_BOARD_H
#define CUPFILLER_HOST_BOARD_H

#include <stdint.h>

#include <string>

namespace cupfiller {
namespace host {

// ---- dong ho --------------------------------------------------------
void use_real_clock();              // dung thoi gian that (che do simulator)
void set_millis(uint32_t ms);       // chuyen sang dong ho ao (che do unit test)
void advance_millis(uint32_t d_ms);
uint32_t millis_now();

// ---- chan GPIO ------------------------------------------------------
void reset_board();                 // xoa trang thai chan + dong ho
void set_pin(int pin, int level);   // gia lap dau vao (cam bien / nut bam)
int get_pin(int pin);               // doc muc chan (ke ca chan output)
uint32_t writes_on(int pin);        // so lan muc chan output thay doi
void clear_writes(int pin);

// ---- kenh UART (may tinh <-> ESP32 ao) ------------------------------
void link_inject(const std::string &from_pc);  // gia lap may tinh gui xuong
std::string link_drain();                      // lay nhung gi ESP32 gui len
size_t link_out_size();                        // so byte dang doi doc

}  // namespace host
}  // namespace cupfiller

#endif  // CUPFILLER_HOST_BOARD_H
