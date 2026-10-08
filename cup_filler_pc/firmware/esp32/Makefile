# =====================================================================
#  Bien dich firmware ESP32 TREN PC ma khong can PlatformIO/Arduino-ESP32
#  (chi can g++). Day la duong chay unit test nhanh nhat; con nap len mach
#  thi dung:  pio run -e esp32dev -t upload
#
#    make test  -> 88 unit test (giao thuc + may trang thai + an toan)
#    make sim   -> "ESP32 ao" noi voi may tinh qua stdin/stdout
#    make check -> ca hai
#
#  Chi bien dich duoc cac file KHONG phu thuoc Arduino:
#    src/uart_protocol.cpp, src/device_fsm.cpp, src/voice_input.cpp
#  (src/*_arduino.cpp va src/main.cpp tu loai vi #if defined(ARDUINO))
# =====================================================================
CXX      ?= g++
CXXFLAGS ?= -std=c++11 -O1 -g -Wall -Wextra -Wno-unused-parameter -Isrc -I.
SRC      := src/uart_protocol.cpp src/device_fsm.cpp src/voice_input.cpp \
            host/board_io_host.cpp

.PHONY: all check test clean

all: test_firmware sim

test_firmware: test/test_firmware/test_firmware.cpp $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $^

sim: host/host_main.cpp $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread

test: test_firmware
	./test_firmware

check: test sim
	@echo "OK: unit test dat, da tao ./sim (ESP32 ao)"

clean:
	rm -f test_firmware sim
