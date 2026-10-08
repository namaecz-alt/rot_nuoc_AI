# =====================================================================
#  Bien dich firmware ESP32 tren PC (khong can Arduino/ESP-IDF) de:
#    make test  -> chay unit test logic ESP32 (giao thuc + may trang thai)
#    make sim   -> tao "ESP32 ao" noi voi may tinh qua stdin/stdout
#  Chi dung duoc cac file: uart_protocol.cpp, device_fsm.cpp,
#  voice_input.cpp (nhung file *_arduino.cpp va .ino chi bien dich tren
#  ESP32 that).
# =====================================================================
CXX      ?= g++
CXXFLAGS ?= -std=c++11 -O1 -g -Wall -Wextra -Wno-unused-parameter -I.
SRC      := uart_protocol.cpp device_fsm.cpp voice_input.cpp host/board_io_host.cpp

.PHONY: all test clean

all: test_firmware sim

test_firmware: test/test_firmware.cpp $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $^

sim: host/host_main.cpp $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $^ -pthread

test: test_firmware
	./test_firmware

clean:
	rm -f test_firmware sim
