import os
import threading
import time
import json
from flask import Flask, request, jsonify
import smbus2

# Config from environment
I2C_BUS = int(os.getenv('I2C_BUS', '1'))
I2C_ADDRESS = int(os.getenv('I2C_ADDRESS', '0x18'), 16)
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', '8080'))
SAMPLE_INTERVAL_MS = int(os.getenv('SAMPLE_INTERVAL_MS', '1000'))

# MCP9808 Register Addresses
REG_TEMP = 0x05
REG_CONFIG = 0x01
REG_ALERT_UPPER = 0x02
REG_ALERT_LOWER = 0x03
REG_ALERT_CRIT = 0x04
REG_MANUF_ID = 0x06
REG_DEVICE_ID = 0x07
REG_RESOLUTION = 0x08

# Alert config bits
ALERT_CONFIG_BITS = {
    "alert_mode": 0x0001,      # Alert mode bit
    "alert_polarity": 0x0002,  # Alert polarity bit
    "alert_select": 0x0004,    # Alert select bit
    "alert_enable": 0x0008     # Alert enable bit
}

bus = smbus2.SMBus(I2C_BUS)
app = Flask(__name__)

# Shared state for sampling
_last_temp_reading = None
_last_temp_timestamp = 0
_sampling_interval = SAMPLE_INTERVAL_MS  # milliseconds
_lock = threading.Lock()

def read_temperature():
    # Read 2 bytes from temperature register
    try:
        data = bus.read_i2c_block_data(I2C_ADDRESS, REG_TEMP, 2)
        t_upper = data[0]
        t_lower = data[1]
        temp = ((t_upper & 0x1F) << 8) | t_lower
        if t_upper & 0x10:  # sign bit
            temp -= 8192
        temperature = temp * 0.0625
        return round(temperature, 4)
    except Exception as e:
        return None

def read_alert_status():
    try:
        # Read config register for alert info
        config = bus.read_word_data(I2C_ADDRESS, REG_CONFIG)
        # Endianness swap
        config = ((config << 8) & 0xFF00) | ((config >> 8) & 0x00FF)
        alert_enabled = bool(config & ALERT_CONFIG_BITS["alert_enable"])
        # Check if alert is asserted (A pin), using upper/lower/crit
        temp = read_temperature()
        upper = get_threshold(REG_ALERT_UPPER)
        lower = get_threshold(REG_ALERT_LOWER)
        crit = get_threshold(REG_ALERT_CRIT)
        alert_status = {
            "enabled": alert_enabled,
            "exceeded_upper": temp is not None and temp > upper,
            "below_lower": temp is not None and temp < lower,
            "exceeded_critical": temp is not None and temp > crit
        }
        return alert_status
    except Exception as e:
        return {"error": "Unable to read alert status"}

def set_sampling_interval(ms):
    global _sampling_interval
    with _lock:
        _sampling_interval = int(ms)

def get_threshold(reg):
    data = bus.read_i2c_block_data(I2C_ADDRESS, reg, 2)
    t_upper = data[0]
    t_lower = data[1]
    temp = ((t_upper & 0x1F) << 8) | t_lower
    if t_upper & 0x10:  # sign bit
        temp -= 8192
    temperature = temp * 0.0625
    return round(temperature, 4)

def set_threshold(reg, value):
    # value: float in Celsius
    temp_raw = int(value / 0.0625)
    if temp_raw < 0:
        temp_raw += 8192
    t_upper = (temp_raw >> 8) & 0x1F
    t_lower = temp_raw & 0xFF
    bus.write_i2c_block_data(I2C_ADDRESS, reg, [t_upper, t_lower])

def set_alert_config(payload):
    # Accepts: {"alert_mode": 0/1, "alert_polarity": 0/1, "alert_select": 0/1, "alert_enable": 0/1, "upper": float, "lower": float, "critical": float}
    config = bus.read_word_data(I2C_ADDRESS, REG_CONFIG)
    config = ((config << 8) & 0xFF00) | ((config >> 8) & 0x00FF)
    for k, bit in ALERT_CONFIG_BITS.items():
        if k in payload:
            if payload[k]:
                config |= bit
            else:
                config &= ~bit
    # Write config
    config_out = ((config << 8) & 0xFF00) | ((config >> 8) & 0x00FF)
    bus.write_word_data(I2C_ADDRESS, REG_CONFIG, config_out)
    # Set thresholds if provided
    if "upper" in payload:
        set_threshold(REG_ALERT_UPPER, float(payload["upper"]))
    if "lower" in payload:
        set_threshold(REG_ALERT_LOWER, float(payload["lower"]))
    if "critical" in payload:
        set_threshold(REG_ALERT_CRIT, float(payload["critical"]))

def set_i2c_address(new_addr):
    # This device does not support software I2C address change; typically hardware pin-based.
    # For this code, simulate by updating global and env.
    global I2C_ADDRESS
    I2C_ADDRESS = int(new_addr, 16) if isinstance(new_addr, str) else int(new_addr)
    os.environ['I2C_ADDRESS'] = hex(I2C_ADDRESS)

# Background thread for temperature sampling
def temp_sampling_worker():
    global _last_temp_reading, _last_temp_timestamp
    while True:
        with _lock:
            interval = _sampling_interval
        temp = read_temperature()
        now = int(time.time() * 1000)
        with _lock:
            _last_temp_reading = temp
            _last_temp_timestamp = now
        time.sleep(interval / 1000.0)

threading.Thread(target=temp_sampling_worker, daemon=True).start()

@app.route('/temp', methods=['GET'])
def api_get_temperature():
    with _lock:
        temp = _last_temp_reading
        ts = _last_temp_timestamp
    if temp is None:
        return jsonify({"error": "Unable to read temperature"}), 500
    # Pagination for logs not implemented, only current value
    return jsonify({"temperature_C": temp, "timestamp_ms": ts})

@app.route('/alert', methods=['GET'])
def api_get_alert():
    status = read_alert_status()
    return jsonify(status)

@app.route('/interval', methods=['PUT'])
def api_set_interval():
    data = request.get_json(force=True)
    interval = data.get("interval_ms") or data.get("interval")
    if not interval or int(interval) <= 0:
        return jsonify({"error": "Invalid interval"}), 400
    set_sampling_interval(int(interval))
    return jsonify({"interval_ms": int(interval)})

@app.route('/alertcfg', methods=['PUT'])
def api_set_alertcfg():
    payload = request.get_json(force=True)
    try:
        set_alert_config(payload)
    except Exception as e:
        return jsonify({"error": "Failed to set alert config", "detail": str(e)}), 400
    return jsonify({"status": "alert configuration updated"})

@app.route('/address', methods=['PUT'])
def api_set_address():
    data = request.get_json(force=True)
    addr = data.get("address")
    if not addr:
        return jsonify({"error": "Missing address"}), 400
    try:
        set_i2c_address(addr)
    except Exception as e:
        return jsonify({"error": "Invalid address", "detail": str(e)}), 400
    return jsonify({"i2c_address": hex(I2C_ADDRESS)})

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)