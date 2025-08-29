import os
import struct
import smbus2
from flask import Flask, jsonify, request, abort

# Environment variable configuration
I2C_BUS = int(os.getenv('I2C_BUS', '1'))
SI7021_I2C_ADDRESS = int(os.getenv('SI7021_I2C_ADDRESS', '0x40'), 16)
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', '8080'))

# SI7021 Commands
CMD_MEASURE_RH_HOLD = 0xE5
CMD_MEASURE_RH_NO_HOLD = 0xF5
CMD_MEASURE_TEMP_HOLD = 0xE3
CMD_MEASURE_TEMP_NO_HOLD = 0xF3
CMD_READ_TEMP_FROM_PREV_RH = 0xE0
CMD_RESET = 0xFE
CMD_WRITE_USER_REG = 0xE6
CMD_READ_USER_REG = 0xE7
CMD_WRITE_HEATER_CTRL = 0x51
CMD_READ_HEATER_CTRL = 0x11
CMD_READ_ID1 = [0xFA, 0x0F]
CMD_READ_ID2 = [0xFC, 0xC9]
CMD_READ_FWREV = [0x84, 0xB8]

app = Flask(__name__)

def get_i2c_bus():
    return smbus2.SMBus(I2C_BUS)

def measure_humidity(hold=True):
    with get_i2c_bus() as bus:
        cmd = CMD_MEASURE_RH_HOLD if hold else CMD_MEASURE_RH_NO_HOLD
        bus.write_byte(SI7021_I2C_ADDRESS, cmd)
        data = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, cmd, 3)
        raw = (data[0] << 8) | data[1]
        humidity = ((125.0 * raw) / 65536.0) - 6.0
        return round(humidity, 2)

def measure_temperature(hold=True):
    with get_i2c_bus() as bus:
        cmd = CMD_MEASURE_TEMP_HOLD if hold else CMD_MEASURE_TEMP_NO_HOLD
        bus.write_byte(SI7021_I2C_ADDRESS, cmd)
        data = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, cmd, 3)
        raw = (data[0] << 8) | data[1]
        temp = ((175.72 * raw) / 65536.0) - 46.85
        return round(temp, 2)

def read_temp_from_last_rh():
    with get_i2c_bus() as bus:
        bus.write_byte(SI7021_I2C_ADDRESS, CMD_READ_TEMP_FROM_PREV_RH)
        data = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, CMD_READ_TEMP_FROM_PREV_RH, 3)
        raw = (data[0] << 8) | data[1]
        temp = ((175.72 * raw) / 65536.0) - 46.85
        return round(temp, 2)

def reset_device():
    with get_i2c_bus() as bus:
        bus.write_byte(SI7021_I2C_ADDRESS, CMD_RESET)

def read_user_register():
    with get_i2c_bus() as bus:
        reg = bus.read_byte_data(SI7021_I2C_ADDRESS, CMD_READ_USER_REG)
        return reg

def write_user_register(value):
    with get_i2c_bus() as bus:
        bus.write_byte_data(SI7021_I2C_ADDRESS, CMD_WRITE_USER_REG, value)

def read_heater_register():
    with get_i2c_bus() as bus:
        reg = bus.read_byte_data(SI7021_I2C_ADDRESS, CMD_READ_HEATER_CTRL)
        return reg

def write_heater_register(value):
    with get_i2c_bus() as bus:
        bus.write_byte_data(SI7021_I2C_ADDRESS, CMD_WRITE_HEATER_CTRL, value)

def read_electronic_id():
    with get_i2c_bus() as bus:
        bus.write_i2c_block_data(SI7021_I2C_ADDRESS, CMD_READ_ID1[0], [CMD_READ_ID1[1]])
        id1 = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, 0, 8)
        bus.write_i2c_block_data(SI7021_I2C_ADDRESS, CMD_READ_ID2[0], [CMD_READ_ID2[1]])
        id2 = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, 0, 6)
        eid = ''.join(['%02X' % b for b in id1[::2]]) + ''.join(['%02X' % b for b in id2[::2]])
        return eid

def read_serial_number():
    with get_i2c_bus() as bus:
        bus.write_i2c_block_data(SI7021_I2C_ADDRESS, CMD_READ_ID1[0], [CMD_READ_ID1[1]])
        id1 = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, 0, 8)
        bus.write_i2c_block_data(SI7021_I2C_ADDRESS, CMD_READ_ID2[0], [CMD_READ_ID2[1]])
        id2 = bus.read_i2c_block_data(SI7021_I2C_ADDRESS, 0, 6)
        sn = (
            '{:02X}{:02X}{:02X}{:02X}{:02X}{:02X}{:02X}{:02X}'
            .format(id1[0], id1[2], id1[4], id1[6], id2[0], id2[1], id2[3], id2[4])
        )
        return sn

def read_firmware_revision():
    with get_i2c_bus() as bus:
        bus.write_i2c_block_data(SI7021_I2C_ADDRESS, CMD_READ_FWREV[0], [CMD_READ_FWREV[1]])
        rev = bus.read_byte(SI7021_I2C_ADDRESS)
        if rev == 0xFF:
            return "1.0"
        elif rev == 0x20:
            return "2.0"
        else:
            return "unknown"

@app.route('/info/sn', methods=['GET'])
def api_get_serial_number():
    sn = read_serial_number()
    return jsonify({'serial_number': sn})

@app.route('/register/user', methods=['GET'])
def api_get_user_register():
    reg = read_user_register()
    return jsonify({'user_register': reg})

@app.route('/register/user', methods=['PUT'])
def api_put_user_register():
    if not request.is_json:
        abort(400, description="Payload must be JSON")
    data = request.get_json()
    if 'value' not in data:
        abort(400, description="Missing 'value' in payload")
    value = data['value']
    if not isinstance(value, int) or not (0 <= value <= 0xFF):
        abort(400, description="'value' must be an integer between 0 and 255")
    write_user_register(value)
    return jsonify({'status': 'success', 'user_register': value})

@app.route('/register/heater', methods=['GET'])
def api_get_heater_register():
    reg = read_heater_register()
    return jsonify({'heater_register': reg})

@app.route('/register/heater', methods=['PUT'])
def api_put_heater_register():
    if not request.is_json:
        abort(400, description="Payload must be JSON")
    data = request.get_json()
    if 'value' not in data:
        abort(400, description="Missing 'value' in payload")
    value = data['value']
    if not isinstance(value, int) or not (0 <= value <= 0xFF):
        abort(400, description="'value' must be an integer between 0 and 255")
    write_heater_register(value)
    return jsonify({'status': 'success', 'heater_register': value})

@app.route('/info/eid', methods=['GET'])
def api_get_electronic_id():
    eid = read_electronic_id()
    return jsonify({'electronic_id': eid})

@app.route('/info/fw', methods=['GET'])
def api_get_firmware_revision():
    fw = read_firmware_revision()
    return jsonify({'firmware_revision': fw})

@app.route('/sensors/humidity', methods=['GET'])
def api_get_humidity():
    humidity = measure_humidity(hold=True)
    return jsonify({'humidity': humidity})

@app.route('/sensors/temperature', methods=['GET'])
def api_get_temperature():
    temperature = measure_temperature(hold=True)
    return jsonify({'temperature': temperature})

@app.route('/commands/reset', methods=['POST'])
def api_post_reset():
    reset_device()
    return jsonify({'status': 'success', 'message': 'Device reset'})

@app.route('/commands/measure', methods=['POST'])
def api_post_measure():
    if not request.is_json:
        abort(400, description="Payload must be JSON")
    data = request.get_json()
    if 'type' not in data:
        abort(400, description="Missing 'type' in payload")
    mtype = data['type']
    result = {}
    if mtype == 'rh_hold':
        result['humidity'] = measure_humidity(hold=True)
    elif mtype == 'rh_no_hold':
        result['humidity'] = measure_humidity(hold=False)
    elif mtype == 'temp_hold':
        result['temperature'] = measure_temperature(hold=True)
    elif mtype == 'temp_no_hold':
        result['temperature'] = measure_temperature(hold=False)
    elif mtype == 'last_rh':
        result['temperature'] = read_temp_from_last_rh()
    else:
        abort(400, description="Unknown measurement type")
    return jsonify(result)

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT)