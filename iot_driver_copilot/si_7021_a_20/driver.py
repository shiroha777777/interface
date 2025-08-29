import os
import struct
import smbus2
from flask import Flask, jsonify, request, abort

# Read configuration from environment variables
I2C_BUS_NUM = int(os.environ.get("I2C_BUS_NUM", "1"))
I2C_ADDR = int(os.environ.get("I2C_ADDR", "0x40"), 16)
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))

app = Flask(__name__)

# Si7021 Command Codes
CMD_MEASURE_RH_HOLD = 0xE5
CMD_MEASURE_RH_NO_HOLD = 0xF5
CMD_MEASURE_TEMP_HOLD = 0xE3
CMD_MEASURE_TEMP_NO_HOLD = 0xF3
CMD_READ_TEMP_FROM_PREV_RH = 0xE0
CMD_RESET = 0xFE

CMD_WRITE_USER_REG = 0xE6
CMD_READ_USER_REG = 0xE7

CMD_WRITE_HEATER_CTRL_REG = 0x51
CMD_READ_HEATER_CTRL_REG = 0x11

CMD_READ_ELECTRONIC_ID_1 = [0xFA, 0x0F]
CMD_READ_ELECTRONIC_ID_2 = [0xFC, 0xC9]
CMD_READ_FIRMWARE_REV = [0x84, 0xB8]

bus = smbus2.SMBus(I2C_BUS_NUM)

def read_measurement(cmd, delay_ms=25):
    bus.write_byte(I2C_ADDR, cmd)
    # The Si7021 datasheet says measurement can take max 12ms, so 25ms is safe.
    import time
    time.sleep(delay_ms / 1000.0)
    data = bus.read_i2c_block_data(I2C_ADDR, 0x00, 3)
    raw = (data[0] << 8) | data[1]
    return raw

def convert_rh(raw):
    # per datasheet: RH = ((125 * raw) / 65536) - 6
    return round(((125.0 * raw) / 65536.0) - 6.0, 2)

def convert_temp(raw):
    # per datasheet: T = ((175.72 * raw) / 65536) - 46.85
    return round(((175.72 * raw) / 65536.0) - 46.85, 2)

@app.route('/info/sn', methods=['GET'])
def get_serial_number():
    # Serial number is split in two parts
    id1 = bus.write_i2c_block_data(I2C_ADDR, CMD_READ_ELECTRONIC_ID_1[0], [CMD_READ_ELECTRONIC_ID_1[1]])
    id1 = bus.read_i2c_block_data(I2C_ADDR, 0x00, 8)
    id2 = bus.write_i2c_block_data(I2C_ADDR, CMD_READ_ELECTRONIC_ID_2[0], [CMD_READ_ELECTRONIC_ID_2[1]])
    id2 = bus.read_i2c_block_data(I2C_ADDR, 0x00, 6)

    # The serial number is the concatenation of bytes 0,2,4,6 (from id1) and 0,1,3,4 (from id2) (odd bytes are CRCs)
    sn_bytes = [
        id1[0], id1[2], id1[4], id1[6],
        id2[0], id2[1], id2[3], id2[4]
    ]
    serial_number = ''.join('{:02X}'.format(x) for x in sn_bytes)
    return jsonify({'serial_number': serial_number})

@app.route('/info/eid', methods=['GET'])
def get_electronic_id():
    # Return all bytes (excluding CRCs) as a list
    bus.write_i2c_block_data(I2C_ADDR, CMD_READ_ELECTRONIC_ID_1[0], [CMD_READ_ELECTRONIC_ID_1[1]])
    id1 = bus.read_i2c_block_data(I2C_ADDR, 0x00, 8)
    bus.write_i2c_block_data(I2C_ADDR, CMD_READ_ELECTRONIC_ID_2[0], [CMD_READ_ELECTRONIC_ID_2[1]])
    id2 = bus.read_i2c_block_data(I2C_ADDR, 0x00, 6)
    bytes_no_crc = [
        id1[0], id1[2], id1[4], id1[6],
        id2[0], id2[1], id2[3], id2[4]
    ]
    return jsonify({'electronic_id_bytes': bytes_no_crc})

@app.route('/info/fw', methods=['GET'])
def get_firmware_revision():
    bus.write_i2c_block_data(I2C_ADDR, CMD_READ_FIRMWARE_REV[0], [CMD_READ_FIRMWARE_REV[1]])
    fw = bus.read_i2c_block_data(I2C_ADDR, 0x00, 1)[0]
    if fw == 0xFF:
        rev = '1.0'
    elif fw == 0x20:
        rev = '2.0'
    else:
        rev = 'unknown'
    return jsonify({'firmware_revision': rev, 'raw': fw})

@app.route('/sensors/humidity', methods=['GET'])
def get_humidity():
    raw = read_measurement(CMD_MEASURE_RH_NO_HOLD)
    humidity = convert_rh(raw)
    return jsonify({'humidity_percent': humidity})

@app.route('/sensors/temperature', methods=['GET'])
def get_temperature():
    raw = read_measurement(CMD_MEASURE_TEMP_NO_HOLD)
    temperature = convert_temp(raw)
    return jsonify({'temperature_celsius': temperature})

@app.route('/register/user', methods=['GET'])
def read_user_register():
    value = bus.read_byte_data(I2C_ADDR, CMD_READ_USER_REG)
    return jsonify({'user_register': value})

@app.route('/register/user', methods=['PUT'])
def write_user_register():
    if not request.is_json:
        abort(400)
    content = request.get_json()
    value = content.get('value')
    if value is None or not (0 <= value <= 0xFF):
        abort(400)
    bus.write_byte_data(I2C_ADDR, CMD_WRITE_USER_REG, value)
    return jsonify({'result': 'success', 'user_register_set': value})

@app.route('/register/heater', methods=['GET'])
def read_heater_register():
    value = bus.read_byte_data(I2C_ADDR, CMD_READ_HEATER_CTRL_REG)
    return jsonify({'heater_register': value})

@app.route('/register/heater', methods=['PUT'])
def write_heater_register():
    if not request.is_json:
        abort(400)
    content = request.get_json()
    value = content.get('value')
    if value is None or not (0 <= value <= 0xFF):
        abort(400)
    bus.write_byte_data(I2C_ADDR, CMD_WRITE_HEATER_CTRL_REG, value)
    return jsonify({'result': 'success', 'heater_register_set': value})

@app.route('/commands/reset', methods=['POST'])
def reset():
    bus.write_byte(I2C_ADDR, CMD_RESET)
    return jsonify({'result': 'success', 'message': 'device reset'})

@app.route('/commands/measure', methods=['POST'])
def measure():
    if not request.is_json:
        abort(400)
    content = request.get_json()
    mtype = content.get('type')
    if mtype == 'rh_hold':
        raw = read_measurement(CMD_MEASURE_RH_HOLD)
        humidity = convert_rh(raw)
        return jsonify({'humidity_percent': humidity})
    elif mtype == 'rh_no_hold':
        raw = read_measurement(CMD_MEASURE_RH_NO_HOLD)
        humidity = convert_rh(raw)
        return jsonify({'humidity_percent': humidity})
    elif mtype == 'temp_hold':
        raw = read_measurement(CMD_MEASURE_TEMP_HOLD)
        temperature = convert_temp(raw)
        return jsonify({'temperature_celsius': temperature})
    elif mtype == 'temp_no_hold':
        raw = read_measurement(CMD_MEASURE_TEMP_NO_HOLD)
        temperature = convert_temp(raw)
        return jsonify({'temperature_celsius': temperature})
    elif mtype == 'last_rh':
        # First trigger a RH measurement, then read last temp
        read_measurement(CMD_MEASURE_RH_NO_HOLD)
        raw = read_measurement(CMD_READ_TEMP_FROM_PREV_RH)
        temperature = convert_temp(raw)
        return jsonify({'temperature_celsius': temperature})
    else:
        abort(400)

if __name__ == "__main__":
    app.run(host=HTTP_HOST, port=HTTP_PORT)