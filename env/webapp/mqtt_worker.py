#Escucha el tópico telemetria/pie publicado por la ESP32-S3. En reposo (IDLE) mantiene solo una ventana deslizante de 250 muestras 
# (~5 segundos) para monitoreo visual, y cuando se una sesión (RECORDING) acumula las tramas hasta que se la terapia.

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
import paho.mqtt.client as mqtt


class TelemetryWorker:
    def __init__(self):
        self.broker = os.getenv("MQTT_BROKER", "192.168.1.74")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.topic = os.getenv("MQTT_TOPIC", "telemetria/pie")

        self._lock = threading.Lock()
        self._preview_buffer = deque(maxlen=250)  # ~5 s a 50 Hz
        self._recording = False
        self._session_buffer: list[dict] = []
        self._session_start_dt: datetime | None = None
        self._session_start_mono: float = 0.0

        self.connected = False
        self.last_msg_ts = 0.0
        self.last_error = ""

        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self._client = mqtt.Client()

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def start(self):
        def _run():
            while True:
                try:
                    self._client.connect(self.broker, self.port, keepalive=30)
                    self._client.loop_forever()
                except Exception as exc:
                    self.connected = False
                    self.last_error = str(exc)
                    time.sleep(3)

        thread = threading.Thread(target=_run, daemon=True, name="mqtt-pie-worker")
        thread.start()

    def _on_connect(self, client, userdata, *args):
        self.connected = True
        self.last_error = ""
        client.subscribe(self.topic, qos=0)

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            pie = payload.get("pie", {})
            now_mono = time.monotonic()

            sample = {
                "ts_mono": now_mono,
                "pitch": float(pie.get("pitch", 0.0)),
                "roll": float(pie.get("roll", 0.0)),
                "yaw": float(pie.get("yaw", 0.0)),
                "vel_angular": float(pie.get("vel_angular", 0.0)),
                "movimiento": str(pie.get("movimiento", "neutro")),
            }

            with self._lock:
                self.last_msg_ts = now_mono
                self._preview_buffer.append(sample)
                if self._recording:
                    sample_rec = dict(sample)
                    sample_rec["t_rel"] = round(now_mono - self._session_start_mono, 3)
                    self._session_buffer.append(sample_rec)
        except Exception:
            pass

    def is_sensor_online(self) -> bool:
        return self.connected and (time.monotonic() - self.last_msg_ts) < 1.5

    def get_preview_snapshot(self) -> tuple[list[dict], bool, int]:
        with self._lock:
            return list(self._preview_buffer), self._recording, len(self._session_buffer)

    def start_session(self):
        with self._lock:
            self._session_buffer = []
            self._session_start_dt = datetime.now(timezone.utc)
            self._session_start_mono = time.monotonic()
            self._recording = True

    def stop_session(self) -> dict:
        with self._lock:
            self._recording = False
            samples = list(self._session_buffer)
            self._session_buffer = []
            inicio_ts = self._session_start_dt or datetime.now(timezone.utc)
            fin_ts = datetime.now(timezone.utc)
            duracion = max(0.1, time.monotonic() - self._session_start_mono)

        return {
            "inicio_ts": inicio_ts,
            "fin_ts": fin_ts,
            "duracion_seg": duracion,
            "samples": samples,
        }