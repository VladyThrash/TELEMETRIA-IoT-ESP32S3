# Sistema IoT de Telemetría Biomecánica del Pie (ESP32-S3)

Este repositorio contiene el firmware en C (basado en ESP-IDF y FreeRTOS) para un sistema de adquisición y análisis de datos biomecánicos en tiempo real enfocado en rehabilitación del pie (`TERAPIA_PIE`). El sistema utiliza una placa ESP32-S3 para leer un sensor inercial **MPU6050** ubicado en el pie a través de un bus I2C por hardware, estimar su orientación tridimensional, clasificar el tipo de movimiento activo y publicar las tramas hacia un broker MQTT.

## Arquitectura del Sistema

El proyecto está diseñado bajo una arquitectura de microservicios IoT y procesamiento asíncrono (Dual-Core) para garantizar un determinismo estricto en el muestreo del sensor a 50 Hz, aislando la latencia de red.

1. **Capa de Hardware:** ESP32-S3 + 1x MPU6050 (montado en el pie).
2. **Capa de Firmware (Edge):** ESP-IDF nativo + FreeRTOS (Colas IPC y tareas ancladas a núcleos).
3. **Capa de Mensajería:** Eclipse Mosquitto (Broker MQTT local).
4. **Capa de Persistencia y Visualización (Stack TIG):** Telegraf + InfluxDB v2 + Grafana (desplegados vía Docker).

## Características del Firmware

* **Bus I2C Único (Fast Mode 400 kHz):** Utiliza la API `driver/i2c_master.h` de ESP-IDF para instanciar un controlador I2C por hardware (`I2C_NUM_0`) con resistencias *pull-up* internas habilitadas, comunicándose con el MPU6050 en la dirección `0x68`.
* **Calibración Automática en Reposo:** Al encender o reiniciar la placa, el sistema toma 100 muestras durante 1 segundo (`mpu6050_calibrate_neutral`) para calcular los *offsets* del giroscopio y establecer la postura neutra de referencia (*tara* de *Pitch* y *Roll*).
* **Acondicionamiento y Fusión Sensorial:**
  * Filtro pasa-bajas digital interno (**DLPF ~44 Hz**) para atenuar vibraciones y temblor muscular, con rangos configurados en `±250 °/s` (giroscopio) y `±2g` (acelerómetro).
  * **Filtro Complementario ($\alpha = 0.96$)** ejecutado cada `20 ms` (50 Hz) para calcular *Pitch* y *Roll*, integración de *Yaw* con banda muerta (`|gz| > 0.6 °/s`) para evitar deriva en reposo y cálculo de magnitud de velocidad angular (`vel_angular`).
  * **Clasificación Cinemática en Tiempo Real:** Identifica automáticamente el ejercicio ejecutado (`dorsiflexion`, `plantarflexion`, `inversion`, `eversion` o `neutro`) según umbrales angulares.
* **Multiprocesamiento (FreeRTOS Dual-Core):**
  * **Core 1 (Tiempo Real — Prioridad 3):** Tarea `sensor_task` dedicada exclusivamente al muestreo I2C a 50 Hz, filtrado matemático y clasificación del movimiento.
  * **Core 0 (Conectividad — Prioridad 2):** Tarea `mqtt_task` que gestiona la reconexión automática Wi-Fi (STA), serializa los datos en JSON (`cJSON`) y los publica mediante `esp_mqtt_client`.
* **Comunicación Segura (IPC):** Transferencia de estructuras `foot_telemetry_t` entre núcleos mediante una cola FreeRTOS (`QueueHandle_t` de 16 elementos).

## Esquema de Conexiones (Pinout)

Conecta el pin `AD0` del MPU6050 a `GND` para fijar su dirección I2C en `0x68`. La alimentación debe tomarse del pin de `3.3V` de la ESP32-S3.

| Componente | Controlador ESP32-S3 | Pin SDA | Pin SCL | Dirección I2C | Alimentación |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Sensor Pie (MPU6050)** | `I2C_NUM_0` | GPIO 8 | GPIO 9 | `0x68` (`AD0` → `GND`) | `3.3V` / `GND` |

## Estructura de Datos (Payload MQTT)

Los datos se publican en el topic `telemetria/pie` con una frecuencia de **50 Hz** (cada `20 ms`) bajo la siguiente estructura JSON:

```json
{
  "pie": {
    "pitch": 12.5,
    "roll": -2.35,
    "yaw": 0.0,
    "vel_angular": 18.42,
    "movimiento": "dorsiflexion"
  }
}
```

### Descripción de Campos

| Campo | Tipo | Unidad | Descripción / Rango |
| :--- | :--- | :--- | :--- |
| `pitch` | `float` | Grados (`°`) | Dorsiflexión (`> 10.0°`) / Flexión plantar (`< -12.0°`). |
| `roll` | `float` | Grados (`°`) | Eversión (`> 10.0°`) / Inversión (`< -10.0°`). |
| `yaw` | `float` | Grados (`°`) | Rotación horizontal acumulada (`-180°` a `180°`). |
| `vel_angular` | `float` | `°/s` | Magnitud total de la velocidad angular ($\sqrt{g_x^2 + g_y^2 + g_z^2}$). |
| `movimiento` | `string` | — | Estado clasificado: `"dorsiflexion"`, `"plantarflexion"`, `"inversion"`, `"eversion"` o `"neutro"`. |

## Requisitos y Dependencias

* **Entorno:** PlatformIO o ESP-IDF v5.2+
* **Framework:** ESP-IDF
* **Componentes ESP-IDF Requeridos:** `esp_wifi`, `esp_event`, `nvs_flash`, `esp_driver_i2c`, `mqtt`, `json` (`cJSON` nativo).

> **Nota para PlatformIO:** Asegúrate de que tu archivo `src/CMakeLists.txt` incluya el registro de componentes de la siguiente manera:
> `idf_component_register(SRCS "main.c" INCLUDE_DIRS "." REQUIRES mqtt json)`

## Configuración y Compilación

1. **Clona este repositorio:**
   ```bash
   git clone https://github.com/VladyThrash/TELEMETRIA-IoT-ESP32S3.git
   cd TELEMETRIA-IoT-ESP32S3
   ```

2. **Configura tus credenciales de red y broker en `src/main.c`:**
   ```c
   #define WIFI_SSID      "TU_RED_WIFI"
   #define WIFI_PASS      "TU_CONTRASEÑA"
   #define BROKER_URI     "mqtt://<IP_DE_TU_BROKER>:1883"
   #define MQTT_TOPIC     "telemetria/pie"
   ```

3. **Compila y sube el firmware a la placa (Ejemplo con PlatformIO):**
   ```bash
   pio run -t upload -t monitor
   ```
   > **Nota:** Mantén el sensor en posición neutra durante el primer segundo tras el encendido mientras finaliza la calibración automática.