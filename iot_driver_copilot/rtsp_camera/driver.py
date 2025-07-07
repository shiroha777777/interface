import os
import threading
import io
import time
from flask import Flask, Response, jsonify, send_file, request
import cv2
import numpy as np

app = Flask(__name__)

# Environment variables
RTSP_URL = os.environ.get('RTSP_URL')  # full RTSP URL, e.g. rtsp://user:pass@ip:port/stream
CAMERA_IP = os.environ.get('CAMERA_IP')  # optional, if RTSP_URL not given
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', 554))
CAMERA_USER = os.environ.get('CAMERA_USER', '')
CAMERA_PASS = os.environ.get('CAMERA_PASS', '')
CAMERA_STREAM_PATH = os.environ.get('CAMERA_STREAM_PATH', 'Streaming/Channels/101')
HTTP_SERVER_HOST = os.environ.get('HTTP_SERVER_HOST', '0.0.0.0')
HTTP_SERVER_PORT = int(os.environ.get('HTTP_SERVER_PORT', 8080))

# Stream state
stream_state = {
    "running": False,
    "thread": None,
    "frame": None,
    "last_frame_time": 0,
    "capture_requested": False,
    "capture_image": None,
    "error": None
}

frame_lock = threading.Lock()

def build_rtsp_url():
    if RTSP_URL:
        return RTSP_URL
    user_pass = ""
    if CAMERA_USER and CAMERA_PASS:
        user_pass = f"{CAMERA_USER}:{CAMERA_PASS}@"
    return f"rtsp://{user_pass}{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"

def video_stream_worker():
    rtsp_url = build_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        with frame_lock:
            stream_state["error"] = "Failed to open RTSP stream"
        return
    with frame_lock:
        stream_state["error"] = None
    while stream_state["running"]:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        with frame_lock:
            stream_state["frame"] = frame
            stream_state["last_frame_time"] = time.time()
            if stream_state["capture_requested"]:
                stream_state["capture_image"] = frame.copy()
                stream_state["capture_requested"] = False
        # Slow down the loop for web streaming, ~25fps
        time.sleep(0.04)
    cap.release()

def start_stream():
    with frame_lock:
        if stream_state["running"]:
            return False
        stream_state["running"] = True
        stream_state["thread"] = threading.Thread(target=video_stream_worker, daemon=True)
        stream_state["thread"].start()
    # Wait for the worker to start and get at least one frame or error
    t0 = time.time()
    while True:
        with frame_lock:
            if stream_state["frame"] is not None or stream_state["error"]:
                break
        if time.time() - t0 > 10:
            break
        time.sleep(0.1)
    return True

def stop_stream():
    with frame_lock:
        stream_state["running"] = False
    if stream_state["thread"]:
        stream_state["thread"].join(timeout=2)
    with frame_lock:
        stream_state["thread"] = None
        stream_state["frame"] = None
        stream_state["capture_image"] = None
    return True

def gen_mjpeg():
    while True:
        with frame_lock:
            frame = stream_state["frame"].copy() if stream_state["frame"] is not None else None
            running = stream_state["running"]
        if not running:
            break
        if frame is not None:
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        else:
            time.sleep(0.04)

@app.route('/stream', methods=['GET'])
def get_stream_status():
    with frame_lock:
        running = stream_state["running"]
        error = stream_state["error"]
    rtsp_url = build_rtsp_url()
    return jsonify({
        "streaming": running,
        "rtsp_url": rtsp_url,
        "http_mjpeg_url": "/stream/live",
        "error": error
    })

@app.route('/stream/live', methods=['GET'])
def stream_live():
    with frame_lock:
        if not stream_state["running"]:
            return Response("Stream is not running.", status=503)
    return Response(gen_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stream/start', methods=['POST'])
def start_stream_api():
    started = start_stream()
    with frame_lock:
        error = stream_state["error"]
    if error:
        return jsonify({"started": False, "error": error}), 500
    return jsonify({
        "started": started,
        "rtsp_url": build_rtsp_url(),
        "http_mjpeg_url": "/stream/live"
    })

@app.route('/stream/stop', methods=['POST'])
def stop_stream_api():
    stopped = stop_stream()
    return jsonify({"stopped": stopped})

@app.route('/capture', methods=['POST'])
def capture_image():
    with frame_lock:
        if not stream_state["running"]:
            return jsonify({"error": "Stream is not running"}), 503
        stream_state["capture_requested"] = True

    # Wait for capture to be processed
    t0 = time.time()
    while True:
        with frame_lock:
            img = stream_state["capture_image"]
        if img is not None or time.time() - t0 > 2:
            break
        time.sleep(0.05)

    with frame_lock:
        if stream_state["capture_image"] is None:
            return jsonify({"error": "Failed to capture image"}), 500
        ret, jpeg = cv2.imencode('.jpg', stream_state["capture_image"])
        stream_state["capture_image"] = None
    if not ret:
        return jsonify({"error": "Failed to encode image"}), 500
    return Response(jpeg.tobytes(), mimetype='image/jpeg')

if __name__ == '__main__':
    app.run(host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT, threaded=True)