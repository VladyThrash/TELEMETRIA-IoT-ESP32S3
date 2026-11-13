#include <stdio.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "driver/i2c_master.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "esp_event.h"

// --- CONFIGURACIÓN DE RED ---
#define WIFI_SSID      "TU_RED_WIFI"
#define WIFI_PASS      "TU_CONTRASEÑA"
#define BROKER_URI     "mqtt://192.168.X.X:1883" // Comando bash: ip a

// --- CONFIGURACIÓN I2C Y MPU6050 ---
#define I2C_MASTER_FREQ_HZ  400000 
#define MPU6050_ADDR        0x68
#define MPU6050_REG_PWR_MGMT_1 0x6B
#define MPU6050_REG_ACCEL_XOUT_H 0x3B

#define KNEE_I2C_PORT 0 // I2C_NUM_0
#define KNEE_SDA_PIN  8
#define KNEE_SCL_PIN  9

#define FOOT_I2C_PORT 1 // I2C_NUM_1
#define FOOT_SDA_PIN  17
#define FOOT_SCL_PIN  18

static const char *TAG = "BIOMECANICA";

// Handles del nuevo driver I2C
i2c_master_bus_handle_t knee_bus_handle;
i2c_master_dev_handle_t knee_dev_handle;

i2c_master_bus_handle_t foot_bus_handle;
i2c_master_dev_handle_t foot_dev_handle;

// Cola y Cliente MQTT
typedef struct {
    float knee_angle;
    float foot_angle;
} sensor_data_t;

QueueHandle_t sensor_queue;
esp_mqtt_client_handle_t mqtt_client;

// --- FUNCIONES DE LA API I2C ---
static esp_err_t i2c_master_init_new(i2c_port_num_t port, int sda, int scl, 
                                     i2c_master_bus_handle_t *bus, 
                                     i2c_master_dev_handle_t *dev) {
    // 1. Configurar el Bus
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = port,
        .sda_io_num = sda,
        .scl_io_num = scl,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_cfg, bus);
    if (err != ESP_OK) return err;

    // 2. Configurar el Dispositivo MPU6050 en ese Bus
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = MPU6050_ADDR,
        .scl_speed_hz = I2C_MASTER_FREQ_HZ,
    };
    return i2c_master_bus_add_device(*bus, &dev_cfg, dev);
}

static esp_err_t mpu6050_wake_up(i2c_master_dev_handle_t dev_handle) {
    uint8_t data[2] = {MPU6050_REG_PWR_MGMT_1, 0x00};
    // timeout_ms = 100
    return i2c_master_transmit(dev_handle, data, sizeof(data), 100); 
}

static float leer_angulo_mpu(i2c_master_dev_handle_t dev_handle) {
    uint8_t reg = MPU6050_REG_ACCEL_XOUT_H;
    uint8_t raw_data[6];
    
    // Transmite el registro a leer y recibe los 6 bytes
    esp_err_t err = i2c_master_transmit_receive(dev_handle, &reg, 1, raw_data, 6, 100);
    
    if (err != ESP_OK) {
        // Validación para pruebas sin sensores conectados.
        return 0.0; 
    }
    
    int16_t accel_x = (raw_data[0] << 8) | raw_data[1];
    int16_t accel_y = (raw_data[2] << 8) | raw_data[3];
    int16_t accel_z = (raw_data[4] << 8) | raw_data[5];

    return atan2(accel_y, sqrt(accel_x * accel_x + accel_z * accel_z)) * 180.0 / 3.14159265;
}

// --- TAREAS DE FREERTOS ---
void sensor_task(void *pvParameters) {
    sensor_data_t current_data;
    while(1) {
        current_data.knee_angle = leer_angulo_mpu(knee_dev_handle);
        current_data.foot_angle = leer_angulo_mpu(foot_dev_handle);

        xQueueSend(sensor_queue, &current_data, pdMS_TO_TICKS(10));
        vTaskDelay(pdMS_TO_TICKS(20)); 
    }
}

void mqtt_publish_task(void *pvParameters) {
    sensor_data_t data;
    while(1) {
        if (xQueueReceive(sensor_queue, &data, portMAX_DELAY)) {
            cJSON *root = cJSON_CreateObject();
            cJSON *pierna = cJSON_CreateObject();
            cJSON_AddNumberToObject(pierna, "rodilla_pitch", data.knee_angle);
            cJSON_AddNumberToObject(pierna, "pie_pitch", data.foot_angle);
            cJSON_AddItemToObject(root, "pierna", pierna);
            
            char *json_str = cJSON_PrintUnformatted(root);
            esp_mqtt_client_publish(mqtt_client, "telemetria/pierna", json_str, 0, 0, 0);
            
            cJSON_Delete(root);
            free(json_str);
        }
    }
}

// --- INICIALIZACIÓN BÁSICA DE RED ---
static void wifi_init_sta(void) {
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
    esp_wifi_connect();
    ESP_LOGI(TAG, "Wi-Fi Iniciado");
}

void app_main(void) {
    nvs_flash_init();
    wifi_init_sta();
    vTaskDelay(pdMS_TO_TICKS(3000)); 
    
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = BROKER_URI,
    };
    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_start(mqtt_client);
    
    // Inicializar el nuevo driver I2C
    i2c_master_init_new(KNEE_I2C_PORT, KNEE_SDA_PIN, KNEE_SCL_PIN, &knee_bus_handle, &knee_dev_handle);
    i2c_master_init_new(FOOT_I2C_PORT, FOOT_SDA_PIN, FOOT_SCL_PIN, &foot_bus_handle, &foot_dev_handle);
    
    mpu6050_wake_up(knee_dev_handle);
    mpu6050_wake_up(foot_dev_handle);
    
    sensor_queue = xQueueCreate(10, sizeof(sensor_data_t));
    
    xTaskCreatePinnedToCore(sensor_task, "sensor_task", 4096, NULL, 3, NULL, 1);
    xTaskCreatePinnedToCore(mqtt_publish_task, "mqtt_task", 4096, NULL, 2, NULL, 0);
}