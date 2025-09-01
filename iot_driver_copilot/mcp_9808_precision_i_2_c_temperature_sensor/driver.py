import os
import json
import threading
import time
from flask import Flask, request, jsonify, Response
import smbus2

# Environment variables for configuration
I2C_BUS_ID = int(os.environ.get('I2C_BUS_ID', '1'))
I2C_ADDRESS = int(os.environ.get('I2C_ADDRESS', '0x18'), 16)
HTTP_HOST = os.environ.get('HTTP_HOST', '0.0.0.0')
HTTP_PORT = int(os.environ.get('HTTP_PORT', '8080'))
SAMPLING_INTERVAL_MS = int(os.environ.get('SAMPLING_INTERVAL_MS', '1000'))

# MCP9808 Register addresses
REG_AMBIENT_TEMP = 0x05
REG_CONFIG = 0x01
REG_UPPER_TEMP = 0x02
REG_LOWER_TEMP = 0x03
REG_CRIT_TEMP = 0x04
REG_MANUF_ID = 0x06
REG_DEVICE_ID = 0x07
REG_RESOLUTION = 0x08

# Default Alert Output config (0x0000: all alert-disabled)
DEFAULT_ALERT_CONFIG = 0x0000

# Global state
sampling_interval_ms = SAMPLING_INTERVAL_MS
current_i2c_address = I2C_ADDRESS
alert_config = DEFAULT_ALERT_CONFIG
temp_log = []
temp_log_lock = threading.Lock()
MAX_LOG_LENGTH = 1000

app = Flask(__name__)

def read_word(bus, addr, reg):
    # Read a 16-bit word and swap bytes
    raw = bus.read_word_data(addr, reg)
    return ((raw << 8) & 0xFF00) | (raw >> 8)

def read_temperature(bus, addr):
    # Returns temperature in Celsius
    raw = read_word(bus, addr, REG_AMBIENT_TEMP)
    temp = raw & 0x0FFF
    temp /= 16.0
    if raw & 0x1000:
        temp -= 256.0
    return round(temp, 4)

def read_alert_status(bus, addr):
    conf = read_word(bus, addr, REG_CONFIG)
    # Bits 2:0 of config register encode alert output status
    alert_output = bool(conf & 0x0008)
    return {
        "alert_output": alert_output,
        "config_register": conf
    }

def write_config(bus, addr, config):
    # Write 16-bit config
    val = ((config & 0xFF) << 8) | ((config >> 8) & 0xFF)
    bus.write_word_data(addr, REG_CONFIG, val)

def set_alert_config(bus, addr, alert_cfg):
    # Set config register
    write_config(bus, addr, alert_cfg)

def set_sampling_interval(interval_ms):
    global sampling_interval_ms
    sampling_interval_ms = max(100, int(interval_ms))

def set_i2c_address(new_addr):
    global current_i2c_address
    current_i2c_address = new_addr

def log_temperature_reading(temp):
    with temp_log_lock:
        ts = int(time.time() * 1000)
        temp_log.append({"timestamp": ts, "temp": temp})
        if len(temp_log) > MAX_LOG_LENGTH:
            temp_log.pop(0)

def temp_sampling_loop():
    bus = smbus2.SMBus(I2C_BUS_ID)
    while True:
        try:
            temp = read_temperature(bus, current_i2c_address)
            log_temperature_reading(temp)
        except Exception:
            pass
        time.sleep(sampling_interval_ms / 1000.0)

@app.route('/temp', methods=['GET'])
def get_temp():
    with temp_log_lock:
        if not temp_log:
            try:
                with smbus2.SMBus(I2C_BUS_ID) as bus:
                    temp = read_temperature(bus, current_i2c_address)
            except Exception as e:
                return jsonify({"error": str(e)}), 500
            log_temperature_reading(temp)
            resp = {"temp": temp, "unit": "C"}
        else:
            resp = {"temp": temp_log[-1]['temp'], "unit": "C"}
        # Support pagination
        start = int(request.args.get('start', '0'))
        limit = int(request.args.get('limit', '1'))
        if request.args.get('log') == '1' or limit > 1:
            resp['log'] = temp_log[start:start+limit]
    return jsonify(resp)

@app.route('/alert', methods=['GET'])
def get_alert():
    try:
        with smbus2.SMBus(I2C_BUS_ID) as bus:
            status = read_alert_status(bus, current_i2c_address)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(status)

@app.route('/alertcfg', methods=['PUT'])
def put_alertcfg():
    data = request.get_json(force=True)
    if not isinstance(data, dict) or "config" not in data:
        return jsonify({"error": "JSON body must have 'config' field (16-bit int)"}), 400
    alert_cfg = int(data["config"])
    try:
        with smbus2.SMBus(I2C_BUS_ID) as bus:
            set_alert_config(bus, current_i2c_address, alert_cfg)
        global alert_config
        alert_config = alert_cfg
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"result": "ok", "config": alert_cfg})

@app.route('/interval', methods=['PUT'])
def put_interval():
    data = request.get_json(force=True)
    if not isinstance(data, dict) or "interval" not in data:
        return jsonify({"error": "JSON body must have 'interval' field (ms)"}), 400
    try:
        interval = int(data["interval"])
        set_sampling_interval(interval)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"result": "ok", "interval": sampling_interval_ms})

@app.route('/address', methods=['PUT'])
def put_address():
    data = request.get_json(force=True)
    if not isinstance(data, dict) or "address" not in data:
        return jsonify({"error": "JSON body must have 'address' field (hex str or int)"}), 400
    addr = data["address"]
    try:
        if isinstance(addr, str):
            if addr.lower().startswith('0x'):
                new_addr = int(addr, 16)
            else:
                new_addr = int(addr)
        else:
            new_addr = int(addr)
        set_i2c_address(new_addr)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"result": "ok", "address": hex(current_i2c_address)})

@app.route('/')
def health():
    return jsonify({"status": "ok", "device": "MCP9808", "address": hex(current_i2c_address)})

def start_sampler():
    t = threading.Thread(target=temp_sampling_loop, daemon=True)
    t.start()

if __name__ == '__main__':
    start_sampler()
    app.run(host=HTTP_HOST, port=HTTP_PORT)