import os
import threading
import time
import queue
from flask import Flask, Response, request, jsonify, stream_with_context

app = Flask(__name__)

# --- Configuration from Environment Variables ---
DEVICE_IP = os.environ.get("DEVICE_IP", "127.0.0.1")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
AUDIO_SAMPLE_RATE = int(os.environ.get("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_SAMPLE_WIDTH = int(os.environ.get("AUDIO_SAMPLE_WIDTH", "2"))  # 16-bit = 2 bytes
AUDIO_CHANNELS = int(os.environ.get("AUDIO_CHANNELS", "1"))
AUDIO_CHUNK_SIZE = int(os.environ.get("AUDIO_CHUNK_SIZE", str(AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH // 10)))  # 100ms

# --- Device State Simulation/Abstraction (replace with hardware access in real use) ---
class DeviceState:
    def __init__(self):
        self.initialized = False
        self.streaming = False
        self.muted = False
        self.frequency = 2405  # MHz, default
        self.hopping_enabled = False
        self.battery_level = 95  # percent
        self.led_status = "green"
        self.rf_channel = 1
        self.crc_result = "OK"
        self.audio_queue = queue.Queue(maxsize=50)
        self.lock = threading.Lock()

    def init_device(self):
        with self.lock:
            self.initialized = True
            self.streaming = False
            self.muted = False
            self.frequency = 2405
            self.hopping_enabled = False
            self.rf_channel = 1
            self.crc_result = "OK"
        return True

    def set_streaming(self, action):
        with self.lock:
            if action == "start":
                self.streaming = True
            elif action == "stop":
                self.streaming = False
        return True

    def set_frequency(self, frequency, hopping_enabled):
        with self.lock:
            self.frequency = frequency
            self.hopping_enabled = hopping_enabled
        return True

    def set_mute(self, mute):
        with self.lock:
            self.muted = mute
        return True

    def get_status(self):
        with self.lock:
            return {
                "initialized": self.initialized,
                "streaming": self.streaming,
                "muted": self.muted,
                "battery_level": self.battery_level,
                "led_status": self.led_status,
                "rf_channel": self.rf_channel,
                "frequency": self.frequency,
                "crc_result": self.crc_result,
            }

    def get_audio_chunk(self):
        try:
            # Simulate PCM audio samples: silence or muted signal
            if not self.streaming or self.muted:
                data = bytes([0] * AUDIO_CHUNK_SIZE)
            else:
                # Simulate simple sine wave or random data for demonstration
                import math
                import struct
                samples = []
                t = time.time()
                freq = 440  # Hz
                for i in range(AUDIO_CHUNK_SIZE // AUDIO_SAMPLE_WIDTH):
                    value = int(32767 * 0.5 * math.sin(2 * math.pi * freq * (t + i / AUDIO_SAMPLE_RATE)))
                    samples.append(struct.pack('<h', value))
                data = b''.join(samples)
            return data
        except Exception:
            return bytes([0] * AUDIO_CHUNK_SIZE)

device_state = DeviceState()

# --- Background Audio Producer Thread for Streaming ---
def audio_producer():
    while True:
        if device_state.streaming:
            chunk = device_state.get_audio_chunk()
            if not device_state.audio_queue.full():
                device_state.audio_queue.put(chunk)
        time.sleep(AUDIO_CHUNK_SIZE / (AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH))

producer_thread = threading.Thread(target=audio_producer, daemon=True)
producer_thread.start()

# --- API Endpoints ---

@app.route("/cmd/init", methods=["POST"])
def cmd_init():
    device_state.init_device()
    return jsonify({"status": "initialized"}), 200

@app.route("/cmd/stream", methods=["POST"])
def cmd_stream():
    body = request.get_json(force=True)
    action = body.get("action", "").lower()
    if action not in ["start", "stop"]:
        return jsonify({"error": "Invalid action"}), 400
    device_state.set_streaming(action)
    return jsonify({"status": f"streaming {action}ed"}), 200

@app.route("/cmd/freq", methods=["POST"])
def cmd_freq():
    body = request.get_json(force=True)
    frequency = body.get("frequency")
    hopping_enabled = body.get("hopping_enabled")
    if frequency is None or hopping_enabled is None:
        return jsonify({"error": "Missing frequency or hopping_enabled"}), 400
    device_state.set_frequency(frequency, bool(hopping_enabled))
    return jsonify({"status": "frequency updated"}), 200

@app.route("/cmd/mute", methods=["POST"])
def cmd_mute():
    body = request.get_json(force=True)
    mute_action = body.get("mute")
    unmute_action = body.get("unmute")
    if mute_action is not None:
        device_state.set_mute(True)
        return jsonify({"status": "audio muted"}), 200
    elif unmute_action is not None:
        device_state.set_mute(False)
        return jsonify({"status": "audio unmuted"}), 200
    else:
        return jsonify({"error": "Specify 'mute' or 'unmute' in request body"}), 400

@app.route("/data/status", methods=["GET"])
def data_status():
    status = device_state.get_status()
    return jsonify(status), 200

@app.route("/data/audio", methods=["GET"])
def data_audio():
    def generate():
        # HTTP streaming: send PCM chunks as binary
        while True:
            if not device_state.streaming:
                time.sleep(0.1)
                continue
            try:
                chunk = device_state.audio_queue.get(timeout=1)
            except queue.Empty:
                chunk = bytes([0] * AUDIO_CHUNK_SIZE)
            yield chunk
    headers = {
        "Content-Type": "audio/L16; rate={}; channels={}".format(AUDIO_SAMPLE_RATE, AUDIO_CHANNELS),
        "Transfer-Encoding": "chunked",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive"
    }
    return Response(stream_with_context(generate()), headers=headers)

# --- Main entry point ---
if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)