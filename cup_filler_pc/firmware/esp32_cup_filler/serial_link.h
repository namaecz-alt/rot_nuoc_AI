// ===========================================================================
//  serial_link.h  -  CẦU NỐI (file chuyển hướng)
//
//  Code THẬT của phần giao tiếp serial với máy tính nằm ở:
//
//      cup_filler_pc/serial_esp32/include/serial_link.h
//
//  Vì sao có file này? Arduino IDE chỉ tìm file .h trong chính thư mục sketch và trong
//  thư mục thư viện, nó KHÔNG biết các thư mục include của PlatformIO. Giữ file này ở
//  đây thì: mở sketch bằng Arduino IDE vẫn biên dịch được, còn VS Code + PlatformIO
//  (mở thư mục cup_filler_pc/serial_esp32) thì dùng thẳng file trong include/.
//  Sửa code serial thì sửa file trong serial_esp32/include/ - đừng sửa file này.
// ===========================================================================
#include "../../serial_esp32/include/serial_link.h"
