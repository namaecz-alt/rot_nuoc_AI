// =====================================================================
//  TUỲ CHỌN: nhận lệnh giọng nói OFFLINE bằng ESP-SR (Espressif)
//  Chỉ biên dịch khi:  -DUSE_ESP_SR=1  và  VOICE_MODE == 3
//
//  ⚠ TRẠNG THÁI: file này KHÔNG được biên dịch trong repo (cần ESP32-S3 +
//  thư viện esp-sr + PSRAM). Nó viết theo API esp-sr 2.x / Arduino-ESP32 3.x.
//  Nếu bạn chưa có ESP32-S3, hãy dùng VOICE_MODE 1 hoặc 2 (module SU-03T /
//  LD3320) — hai chế độ đó đã được kiểm chứng bằng unit test.
//
//  Phần cứng: ESP32-S3 DevKit + mic I2S INMP441
//      INMP441  WS   -> GPIO4      (I2S_WS)
//      INMP441  SCK  -> GPIO5      (I2S_SCK)
//      INMP441  SD   -> GPIO6      (I2S_SD)
//      INMP441  L/R  -> GND        (kênh trái)
//      INMP441  VDD  -> 3.3 V
//  Model:  esp-sr (WakeNet + MultiNet). MultiNet hỗ trợ tiếng Anh/Trung;
//  tiếng Việt nên dùng module ngoài (VOICE_MODE 1/2).
//
//  Câu lệnh dùng ở đây (sửa esp_mn_commands_add nếu bạn muốn khác):
//      "level one" .. "level five"  -> chọn mức 1..5 và bắt đầu rót
//      "start fill"                 -> rót mức đang chọn
//      "stop fill"                  -> dừng
// =====================================================================
#if defined(USE_ESP_SR) && (VOICE_MODE == 3)

#include <string.h>

#include "driver/i2s.h"
#include "esp_log.h"
#include "esp_mn_iface.h"
#include "esp_mn_models.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"

#include "voice_input.h"

#define I2S_WS GPIO_NUM_4
#define I2S_SCK GPIO_NUM_5
#define I2S_SD GPIO_NUM_6
#define I2S_PORT I2S_NUM_0
#define TAG "voice_sr"

namespace cupfiller {
namespace {

const int kSampleRate = 16000;
int16_t *g_audio = nullptr;
int g_chunk = 0;

esp_wn_iface_t *g_wn = nullptr;
model_iface_data_t *g_wn_data = nullptr;
esp_mn_iface_t *g_mn = nullptr;
model_iface_data_t *g_mn_data = nullptr;
int g_wn_chunk = 0;
bool g_ready = false;

void i2s_init() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = kSampleRate;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 4;
  cfg.dma_buf_len = 512;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_SCK;
  pins.ws_io_num = I2S_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = I2S_SD;

  if (i2s_driver_install(I2S_PORT, &cfg, 0, nullptr) != ESP_OK) {
    ESP_LOGE(TAG, "i2s_driver_install that bai");
    return;
  }
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

}  // namespace

void voice_begin() {
  i2s_init();

  srmodel_list_t *models = esp_srmodel_init("model");
  if (models == nullptr) {
    ESP_LOGE(TAG, "khong tim thay model esp-sr trong partition 'model'");
    return;
  }
  char *wn_name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
  if (wn_name == nullptr) {
    ESP_LOGE(TAG, "thieu model WakeNet");
    return;
  }
  g_wn = (esp_wn_iface_t *)esp_wn_handle_from_name(wn_name);
  g_wn_data = g_wn->create(wn_name, DET_MODE_90);
  g_wn_chunk = g_wn->get_samp_chunksize(g_wn_data);

  char *mn_name = esp_srmodel_filter(models, ESP_MN_PREFIX, ESP_MN_ENGLISH);
  if (mn_name == nullptr) {
    ESP_LOGE(TAG, "thieu model MultiNet (tieng Anh)");
    return;
  }
  g_mn = (esp_mn_iface_t *)esp_mn_handle_from_name(mn_name);
  g_mn_data = g_mn->create(mn_name, 6000);

  esp_mn_commands_clear();
  esp_mn_commands_add("level one");    // 0
  esp_mn_commands_add("level two");    // 1
  esp_mn_commands_add("level three");  // 2
  esp_mn_commands_add("level four");   // 3
  esp_mn_commands_add("level five");   // 4
  esp_mn_commands_add("start fill");   // 5
  esp_mn_commands_add("stop fill");    // 6
  esp_mn_commands_update(g_mn, g_mn_data);
  g_mn->print_active_speech_commands(g_mn_data);

  g_chunk = g_wn_chunk;
  g_audio = (int16_t *)malloc(sizeof(int16_t) * g_chunk);
  g_ready = (g_audio != nullptr);
  ESP_LOGI(TAG, "ESP-SR san sang, chunk = %d mau", g_chunk);
}

int voice_poll(uint32_t /*now_ms*/) {
  if (!g_ready) return VOICE_NONE;

  size_t got = 0;
  if (i2s_read(I2S_PORT, g_audio, sizeof(int16_t) * g_chunk, &got, portMAX_DELAY) != ESP_OK) {
    return VOICE_NONE;
  }
  if (got < sizeof(int16_t) * g_chunk) return VOICE_NONE;

  wakenet_state_t ws = g_wn->detect(g_wn_data, g_audio);
  if (ws == WAKENET_CHANNEL_VERIFIED) {
    ESP_LOGI(TAG, "nghe thay tu danh thuc -> cho lenh");
    return VOICE_NONE;
  }

  esp_mn_state_t ms = g_mn->detect(g_mn_data, g_audio);
  if (ms == ESP_MN_STATE_DETECTED) {
    esp_mn_results_t *res = g_mn->get_results(g_mn_data);
    if (res != nullptr && res->num > 0) {
      const int id = res->command_id[0];  // 0..6 theo thu tu da dang ky
      ESP_LOGI(TAG, "lenh giong noi id=%d", id);
      if (id >= VOICE_LEVEL_1 && id <= VOICE_STOP) return id;
    }
  }
  return VOICE_NONE;
}

}  // namespace cupfiller

#endif  // USE_ESP_SR && VOICE_MODE == 3
