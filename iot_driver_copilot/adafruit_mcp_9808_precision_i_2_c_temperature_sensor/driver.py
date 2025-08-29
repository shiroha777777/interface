import os
import json
from flask import Flask, jsonify
import smbus2
import threading

# Configuration from environment variables
I2C_BUS_NUMBER = int(os.environ.get("I2C_BUS_NUMBER", "1"))
I2C_ADDRESS = int(os.environ.get("I2C_ADDRESS", "0x18"), 16)
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))

# MCP9808 Register Addresses
MCP9808_REG_AMBIENT_TEMP = 0x05

# Thread lock for I2C access
i2c_lock = threading.Lock()

def read_temperature():
    with i2c_lock:
        bus = smbus2.SMBus(I2C_BUS_NUMBER)
        try:
            data = bus.read_i2c_block_data(I2C_ADDRESS, MCP9808_REG_AMBIENT_TEMP, 2)
        finally:
            bus.close()
    t_upper = data[0]
    t_lower = data[1]
    temp = ((t_upper & 0x1F) << 8) | t_lower
    if t_upper & 0x10:
        temp -= 8192
    celsius = temp * 0.0625
    return round(celsius, 4)

app = Flask(__name__)

@app.route('/sensors/temperature', methods=['GET'])
def get_temperature():
    try:
        temp_c = read_temperature()
        return jsonify({"temperature_celsius": temp_c})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host=HTTP_HOST, port=HTTP_PORT)