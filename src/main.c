#include <stdio.h>
#include <string.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/i2c_master.h"
#include "mqtt_client.h"
#include "cJSON.h"

// --- CONFIGURACIÓN DE RED (AJUSTAR A TU RED LOCAL) ---
#define WIFI_SSID      "TU_RED_WIFI"
#define WIFI_PASS      "TU_CONTRASEÑA"
#define BROKER_URI     "mqtt://192.168.1.74:1883" // Verificar con: ip a
#define MQTT_TOPIC     "telemetria/pie"

// --- CONFIGURACIÓN I2C Y REGISTROS MPU6050 ---
#define I2C_MASTER_FREQ_HZ       400000
#define FOOT_I2C_PORT            I2C_NUM_0
#define FOOT_SDA_PIN             8
#define FOOT_SCL_PIN             9

#define MPU6050_ADDR             0x68
#define MPU6050_REG_CONFIG       0x1A
#define MPU6050_REG_GYRO_CONFIG  0x1B
#define MPU6050_REG_ACCEL_CONFIG 0x1C
#define MPU6050_REG_ACCEL_XOUT_H 0x3B
#define MPU6050_REG_PWR_MGMT_1   0x6B

// --- PARÁMETROS DE FILTRADO Y MUESTREO ---
#define SAMPLE_PERIOD_MS         20
#define DT_SEC                   (SAMPLE_PERIOD_MS / 1000.0f)
#define COMP_FILTER_ALPHA        0.96f
#define RAD_TO_DEG               57.2957795f

static const char *TAG = "TERAPIA_PIE";

i2c_master_bus_handle_t foot_bus_handle;
i2c_master_dev_handle_t foot_dev_handle;

typedef struct {
    float pitch;        // Dorsiflexión (+) / Flexión plantar (-) [grados]
    float roll;         // Eversión (+) / Inversión (-) [grados]
    float yaw;          // Rotación horizontal acumulada [grados]
    float vel_angular;  // Magnitud de velocidad angular [grados/s]
    const char *estado; // Clasificación del ejercicio activo
} foot_telemetry_t;

// Offsets de calibración (tara en reposo)
static float gx_offset = 0.0f, gy_offset = 0.0f, gz_offset = 0.0f;
static float pitch_offset = 0.0f, roll_offset = 0.0f;

QueueHandle_t sensor_queue;
esp_mqtt_client_handle_t mqtt_client;

// --- INICIALIZACIÓN I2C Y MPU6050 ---
static esp_err_t i2c_foot_init(void) {
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = FOOT_I2C_PORT,
        .sda_io_num = FOOT_SDA_PIN,
        .scl_io_num = FOOT_SCL_PIN,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_cfg, &foot_bus_handle);
    if (err != ESP_OK) return err;

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = MPU6050_ADDR,
        .scl_speed_hz = I2C_MASTER_FREQ_HZ,
    };
    return i2c_master_bus_add_device(foot_bus_handle, &dev_cfg, &foot_dev_handle);
}

static esp_err_t mpu6050_write_reg(uint8_t reg, uint8_t val) {
    uint8_t data[2] = {reg, val};
    return i2c_master_transmit(foot_dev_handle, data, sizeof(data), 100);
}

static esp_err_t mpu6050_init_sensor(void) {
    esp_err_t err;
    // Despertar sensor usando reloj PLL con referencia en eje X
    err = mpu6050_write_reg(MPU6050_REG_PWR_MGMT_1, 0x01);
    if (err != ESP_OK) return err;
    vTaskDelay(pdMS_TO_TICKS(50));

    // Filtro pasa-bajas digital (DLPF ~44Hz) para suprimir temblor muscular
    mpu6050_write_reg(MPU6050_REG_CONFIG, 0x03);
    // Rango giroscopio: +-250 deg/s (131.0 LSB/deg/s)
    mpu6050_write_reg(MPU6050_REG_GYRO_CONFIG, 0x00);
    // Rango acelerómetro: +-2g (16384.0 LSB/g)
    mpu6050_write_reg(MPU6050_REG_ACCEL_CONFIG, 0x00);
    return ESP_OK;
}

static esp_err_t mpu6050_read_raw(float *ax, float *ay, float *az,
                                  float *gx, float *gy, float *gz) {
    uint8_t reg = MPU6050_REG_ACCEL_XOUT_H;
    uint8_t raw[14];

    esp_err_t err = i2c_master_transmit_receive(foot_dev_handle, &reg, 1, raw, sizeof(raw), 100);
    if (err != ESP_OK) return err;

    int16_t raw_ax = (int16_t)((raw[0] << 8) | raw[1]);
    int16_t raw_ay = (int16_t)((raw[2] << 8) | raw[3]);
    int16_t raw_az = (int16_t)((raw[4] << 8) | raw[5]);
    // raw[6..7] corresponde a temperatura interna
    int16_t raw_gx = (int16_t)((raw[8] << 8) | raw[9]);
    int16_t raw_gy = (int16_t)((raw[10] << 8) | raw[11]);
    int16_t raw_gz = (int16_t)((raw[12] << 8) | raw[13]);

    *ax = raw_ax / 16384.0f;
    *ay = raw_ay / 16384.0f;
    *az = raw_az / 16384.0f;

    *gx = raw_gx / 131.0f;
    *gy = raw_gy / 131.0f;
    *gz = raw_gz / 131.0f;

    return ESP_OK;
}

static void mpu6050_calibrate_neutral(void) {
    const int samples = 100; // 1 segundo en reposo
    float sum_gx = 0, sum_gy = 0, sum_gz = 0;
    float sum_pitch = 0, sum_roll = 0;
    int valid = 0;

    ESP_LOGI(TAG, "Calibrando posición neutra del pie... Mantenga el pie quieto.");
    for (int i = 0; i < samples; i++) {
        float ax, ay, az, gx, gy, gz;
        if (mpu6050_read_raw(&ax, &ay, &az, &gx, &gy, &gz) == ESP_OK) {
            sum_gx += gx;
            sum_gy += gy;
            sum_gz += gz;
            sum_pitch += atan2f(ay, sqrtf(ax * ax + az * az)) * RAD_TO_DEG;
            sum_roll  += atan2f(ax, sqrtf(ay * ay + az * az)) * RAD_TO_DEG;
            valid++;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    if (valid > 0) {
        gx_offset = sum_gx / valid;
        gy_offset = sum_gy / valid;
        gz_offset = sum_gz / valid;
        pitch_offset = sum_pitch / valid;
        roll_offset  = sum_roll / valid;
        ESP_LOGI(TAG, "Calibración lista (Offset Pitch: %.2f°, Roll: %.2f°)", pitch_offset, roll_offset);
    } else {
        ESP_LOGE(TAG, "No se detectó el MPU6050 en 0x68. Revisa SDA(GPIO8)/SCL(GPIO9)/AD0.");
    }
}

static const char* clasificar_movimiento(float pitch, float roll) {
    if (pitch > 10.0f)  return "dorsiflexion";
    if (pitch < -12.0f) return "plantarflexion";
    if (roll < -10.0f)  return "inversion";
    if (roll > 10.0f)   return "eversion";
    return "neutro";
}

// --- TAREAS DE FREERTOS ---
void sensor_task(void *pvParameters) {
    float pitch = 0.0f, roll = 0.0f, yaw = 0.0f;
    foot_telemetry_t data;

    while (1) {
        float ax, ay, az, gx, gy, gz;
        if (mpu6050_read_raw(&ax, &ay, &az, &gx, &gy, &gz) == ESP_OK) {
            gx -= gx_offset;
            gy -= gy_offset;
            gz -= gz_offset;

            float accel_pitch = atan2f(ay, sqrtf(ax * ax + az * az)) * RAD_TO_DEG - pitch_offset;
            float accel_roll  = atan2f(ax, sqrtf(ay * ay + az * az)) * RAD_TO_DEG - roll_offset;

            // Filtro complementario (96% giroscopio, 4% acelerómetro)
            pitch = COMP_FILTER_ALPHA * (pitch + gx * DT_SEC) + (1.0f - COMP_FILTER_ALPHA) * accel_pitch;
            roll  = COMP_FILTER_ALPHA * (roll  + gy * DT_SEC) + (1.0f - COMP_FILTER_ALPHA) * accel_roll;

            // Banda muerta en Yaw para evitar deriva en reposo
            if (fabsf(gz) > 0.6f) {
                yaw += gz * DT_SEC;
                if (yaw > 180.0f) yaw -= 360.0f;
                if (yaw < -180.0f) yaw += 360.0f;
            }

            data.pitch = roundf(pitch * 100.0f) / 100.0f;
            data.roll  = roundf(roll * 100.0f) / 100.0f;
            data.yaw   = roundf(yaw * 100.0f) / 100.0f;
            data.vel_angular = roundf(sqrtf(gx * gx + gy * gy + gz * gz) * 100.0f) / 100.0f;
            data.estado = clasificar_movimiento(data.pitch, data.roll);

            xQueueSend(sensor_queue, &data, 0);
        } else {
            ESP_LOGW(TAG, "Error de lectura I2C en MPU6050");
        }
        vTaskDelay(pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
    }
}

void mqtt_publish_task(void *pvParameters) {
    foot_telemetry_t data;
    while (1) {
        if (xQueueReceive(sensor_queue, &data, portMAX_DELAY)) {
            cJSON *root = cJSON_CreateObject();
            cJSON *pie = cJSON_CreateObject();

            cJSON_AddNumberToObject(pie, "pitch", data.pitch);
            cJSON_AddNumberToObject(pie, "roll", data.roll);
            cJSON_AddNumberToObject(pie, "yaw", data.yaw);
            cJSON_AddNumberToObject(pie, "vel_angular", data.vel_angular);
            cJSON_AddStringToObject(pie, "movimiento", data.estado);
            cJSON_AddItemToObject(root, "pie", pie);

            char *json_str = cJSON_PrintUnformatted(root);
            if (json_str != NULL) {
                esp_mqtt_client_publish(mqtt_client, MQTT_TOPIC, json_str, 0, 0, 0);
                free(json_str);
            }
            cJSON_Delete(root);
        }
    }
}

// --- GESTIÓN DE WIFI CON RECONEXIÓN AUTOMÁTICA ---
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi desconectado, reintentando...");
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "IP asignada: " IPSTR, IP2STR(&event->ip_info.ip));
    }
}

static void wifi_init_sta(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_sta();

    ESP_ERROR_CHECK(i2c_foot_init());
    if (mpu6050_init_sensor() == ESP_OK) {
        mpu6050_calibrate_neutral();
    }

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = BROKER_URI,
    };
    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_start(mqtt_client);

    sensor_queue = xQueueCreate(16, sizeof(foot_telemetry_t));

    xTaskCreatePinnedToCore(sensor_task, "sensor_task", 4096, NULL, 3, NULL, 1);
    xTaskCreatePinnedToCore(mqtt_publish_task, "mqtt_task", 4096, NULL, 2, NULL, 0);
}