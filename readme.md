# Sistema IoT de Telemetría Biomecánica (ESP32-S3)

Este repositorio contiene el firmware en C (basado en ESP-IDF y FreeRTOS) para un sistema de adquisición de datos biomecánicos en tiempo real. El sistema utiliza una placa ESP32-S3 para leer simultáneamente dos sensores inerciales MPU6050 (ubicados en la rodilla y el pie) a través de dos buses I2C físicos independientes, procesando los ángulos de inclinación y publicándolos hacia un broker MQTT.

## Arquitectura del Sistema

El proyecto está diseñado bajo una arquitectura de microservicios IoT y procesamiento asíncrono (Dual-Core) para garantizar un determinismo estricto en el muestreo de los sensores, aislando la latencia de red.

1. **Capa de Hardware:** ESP32-S3 + 2x MPU6050.
2. **Capa de Firmware (Edge):** ESP-IDF nativo + FreeRTOS (Colas IPC y Tareas Ancladas a Núcleos).
3. **Capa de Mensajería:** Eclipse Mosquitto (Broker MQTT local).
4. **Capa de Persistencia y Visualización (Stack TIG):** Telegraf + InfluxDB v2 + Grafana (Desplegados vía Docker).

## Características del Firmware

* **I2C Dual-Bus:** Utiliza la nueva API `driver/i2c_master.h` de ESP-IDF para instanciar dos controladores I2C por hardware (`I2C_NUM_0` e `I2C_NUM_1`), evitando colisiones de direcciones (ambos MPU6050 operan en `0x68`).
* **Multiprocesamiento (FreeRTOS):**
  * **Core 1 (Tiempo Real):** Tarea de alta prioridad dedicada exclusivamente a la lectura iterativa de los giroscopios/acelerómetros y la conversión matemática de los cuaterniones/ángulos (Pitch/Roll).
  * **Core 0 (Conectividad):** Tarea que maneja el stack TCP/IP, arma la estructura JSON (`cJSON`) y publica los datos vía `esp_mqtt_client`.
* **Comunicación Segura (IPC):** Transferencia atómica de datos entre núcleos mediante `QueueHandle_t`.

## 🔌 Esquema de Conexiones (Pinout)

Se deben conectar los pines `AD0` de **ambos** sensores a `GND`. La alimentación debe provenir del pin de `3.3V` de la ESP32-S3.

| Componente | Controlador ESP32-S3 | Pin SDA | Pin SCL | Dirección I2C |
| :--- | :--- | :--- | :--- | :--- |
| **Sensor Rodilla** | `I2C_NUM_0` | GPIO 8 | GPIO 9 | `0x68` |
| **Sensor Pie** | `I2C_NUM_1` | GPIO 17 | GPIO 18 | `0x68` |

## Estructura de Datos (Payload MQTT)

Los datos se publican en el topic `telemetria/pierna` con una frecuencia de 50Hz (cada 20ms) bajo la siguiente estructura JSON:

```json
{
  "pierna": {
    "rodilla_pitch": 45.2,
    "pie_pitch": 12.5
  }
}
```

## Requisitos y Dependencias

- **Entorno:** PlatformIO o ESP-IDF v5.2+
- **Framework:** ESP-IDF
- **Componentes ESP-IDF Requeridos:** `esp_wifi`, `mqtt`, `json` (cJSON nativo).

> **Nota para PlatformIO:** Asegúrate de que tu archivo `src/CMakeLists.txt` incluya el registro de componentes de la siguiente manera:
> `idf_component_register(SRCS "main.c" INCLUDE_DIRS "." REQUIRES mqtt json)`

## Configuración y Compilación

1. **Clona este repositorio:**
   ```bash
   git clone https://github.com/TU_USUARIO/TU_REPOSITORIO.git
   cd TU_REPOSITORIO
   ```

2. **Configura tus credenciales de red en `src/main.c`:**
   ```c
   #define WIFI_SSID      "TU_RED_WIFI"
   #define WIFI_PASS      "TU_CONTRASEÑA"
   #define BROKER_URI     "mqtt://<IP_DE_TU_BROKER>:1883"
   ```

3. **Compila y sube el firmware a la placa (Ejemplo con PlatformIO):**
   ```bash
   pio run -t upload -t monitor
   ```