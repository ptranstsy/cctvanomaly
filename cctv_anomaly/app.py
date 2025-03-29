import cv2
import numpy as np
from ultralytics import YOLO
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from flask import Flask, Response, render_template, request, redirect, url_for, flash, jsonify
import requests

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Diperlukan untuk flash messages

# Simulasi database user (gantilah dengan Firebase jika diperlukan)
users = {"user@example.com": "password123"}

# Load model YOLOv8
model = YOLO("yolov8n.pt")

# Inisialisasi Firebase
try:
    cred = credentials.Certificate(r"C:\Users\User\cctv_anomaly\serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    print("Firebase terhubung")
except Exception as e:
    print(f"Error Firebase: {e}")
db = firestore.client()

# Ganti dengan URL Webhook Microsoft Teams Anda
TEAMS_WEBHOOK_URL = "YOUR_TEAMS_WEBHOOK_URL_HERE"  # Replace with your actual Teams webhook URL

# Simulasi email pengguna (akan diupdate setelah login)
CURRENT_USER_EMAIL = None  # Akan diisi setelah login berhasil

# Dictionary untuk menyimpan objek VideoCapture dan IP
caps = {1: None, 2: None, 3: None, 4: None}
camera_ips = {1: "", 2: "", 3: "", 4: ""}

# CamID untuk setiap CCTV
cam_ids = {1: "C001", 2: "C002", 3: "C003", 4: "C004"}

# Daftar objek berbahaya
dangerous_objects = {
    "knife": "High", "gun": "High", "person": "Low", "fire": "High",
    "suspicious_bag": "Medium", "fight": "High"
}

# Menyimpan status alert untuk setiap kamera
detected_alerts = {1: set(), 2: set(), 3: set(), 4: set()}

# Fungsi untuk mengirim pesan ke Microsoft Teams
def send_teams_message(cam_id, label, priority):
    global CURRENT_USER_EMAIL
    message = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": "CCTV Anomaly Detected",
        "title": "CCTV Anomaly Alert",
        "sections": [{
            "activityTitle": f"Anomaly Detected by {cam_id}",
            "facts": [
                {"name": "Detected Object", "value": label},
                {"name": "Priority", "value": priority},
                {"name": "Time", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                {"name": "Reported by", "value": CURRENT_USER_EMAIL or "Unknown"}
            ],
            "text": "An anomaly has been detected by the CCTV system."
        }]
    }
    try:
        response = requests.post(TEAMS_WEBHOOK_URL, json=message)
        response.raise_for_status()
        print(f"Pesan Teams terkirim untuk {label} di {cam_id}")
    except Exception as e:
        print(f"Gagal mengirim pesan ke Teams: {e}")

# Fungsi untuk menginisialisasi atau mengupdate kamera dengan retry
def initialize_camera(cam_num, max_retries=3):
    global caps
    if caps[cam_num]:
        caps[cam_num].release()
    retries = 0
    while retries < max_retries:
        if camera_ips[cam_num]:
            caps[cam_num] = cv2.VideoCapture(camera_ips[cam_num])
        else:
            caps[cam_num] = cv2.VideoCapture(0)  # Default to webcam if no IP
        if caps[cam_num] and caps[cam_num].isOpened():
            print(f"Kamera {cam_num} berhasil diinisialisasi")
            return
        retries += 1
        print(f"Retry {retries}/{max_retries} untuk kamera {cam_num}")
    print(f"Gagal menginisialisasi kamera {cam_num} setelah {max_retries} percobaan")
    caps[cam_num] = None

# Fungsi untuk menghasilkan frame deteksi
def generate_frames(cam_num):
    if not camera_ips[cam_num]:
        initialize_camera(cam_num)
    
    cap = caps[cam_num]
    cam_id = cam_ids[cam_num]
    
    while True:
        if not cap or not cap.isOpened():
            print(f"Kamera {cam_num} tidak tersedia, mencoba reconnect...")
            initialize_camera(cam_num)
            cap = caps[cam_num]
            continue

        ret, frame = cap.read()
        if not ret:
            print(f"Gagal membaca frame dari CCTV {cam_num}, memulai ulang capture...")
            initialize_camera(cam_num)
            cap = caps[cam_num]
            continue

        results = model(frame)
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                label = result.names[int(box.cls[0])]
                confidence = box.conf[0].item()
                if label in dangerous_objects and confidence > 0.5:
                    priority = dangerous_objects[label]
                    color = (0, 0, 255) if priority == "High" else (0, 165, 255) if priority == "Medium" else (0, 255, 0)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, f"{label} ({priority})", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                    alert_key = f"{label}_{cam_id}"
                    if alert_key not in detected_alerts[cam_num]:
                        detected_alerts[cam_num].add(alert_key)
                        alert_data = {
                            "Level": priority,
                            "Description": label,
                            "Date": datetime.now().isoformat(),
                            "CamID": cam_id
                        }
                        db.collection("alerts").add(alert_data)
                        print(f"🔥 Alert terkirim dari CCTV {cam_num}: {alert_data}")
                        send_teams_message(cam_id, label, priority)

        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            print(f"Gagal mengencode frame untuk CCTV {cam_num}")
            continue
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# Route untuk halaman utama (redirect ke login)
@app.route('/')
def home():
    return redirect(url_for('login'))

# Route untuk login
@app.route('/login', methods=['GET', 'POST'])
def login():
    global CURRENT_USER_EMAIL
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        if email in users and users[email] == password:
            CURRENT_USER_EMAIL = email  # Set email pengguna setelah login
            return redirect(url_for('dashboard'))
        else:
            flash("Incorrect email or password!", "error")
    return render_template('login.html')

# Route untuk logout
@app.route('/logout')
def logout():
    global CURRENT_USER_EMAIL
    CURRENT_USER_EMAIL = None  # Reset email pengguna
    return redirect(url_for('login'))

# Route untuk dashboard
@app.route('/dashboard')
def dashboard():
    if CURRENT_USER_EMAIL is None:  # Cek apakah user sudah login
        return redirect(url_for('login'))
    return render_template('dashboard.html', user_email=CURRENT_USER_EMAIL)

# Route untuk set IP kamera
@app.route('/set_ip', methods=['POST'])
def set_ip():
    if CURRENT_USER_EMAIL is None:  # Cek login
        return jsonify({'message': 'Please log in first'}), 401
    data = request.get_json()
    cam_num = data.get('cam_num')
    ip = data.get('ip')
    
    if cam_num not in [1, 2, 3, 4]:
        return jsonify({'message': 'Invalid camera number'}), 400
    
    camera_ips[cam_num] = ip
    initialize_camera(cam_num)
    
    return jsonify({'message': f'IP for CCTV {cam_num} set to {ip}'})

# Route untuk popup CCTV manage
@app.route('/CCTVmanage')
def manage_cctv_popup():
    if CURRENT_USER_EMAIL is None:  # Cek login
        return redirect(url_for('login'))
    return render_template('CCTVmanage.html')

# Route untuk video feed setiap kamera
@app.route('/video_feed/1')
def video_feed_1():
    if CURRENT_USER_EMAIL is None:  # Cek login
        return redirect(url_for('login'))
    return Response(generate_frames(1), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/video_feed/2')
def video_feed_2():
    if CURRENT_USER_EMAIL is None:
        return redirect(url_for('login'))
    return Response(generate_frames(2), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/video_feed/3')
def video_feed_3():
    if CURRENT_USER_EMAIL is None:
        return redirect(url_for('login'))
    return Response(generate_frames(3), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/video_feed/4')
def video_feed_4():
    if CURRENT_USER_EMAIL is None:
        return redirect(url_for('login'))
    return Response(generate_frames(4), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    print("Server akan dimulai...")
    for cam_num in caps.keys():
        initialize_camera(cam_num)
    print("Registered routes:")
    print(app.url_map)
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=True)
    for cap in caps.values():
        if cap:
            cap.release()
    cv2.destroyAllWindows()