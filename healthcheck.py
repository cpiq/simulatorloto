import time
import requests
from datetime import datetime

APP_URL = "https://simulatorloto.onrender.com/"

INTERVAL_SECONDS =  5*60
TIMEOUT_SECONDS = 30

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def ping():
    for attempt in range(1, 3):
        try:
            r = requests.get(APP_URL, timeout=TIMEOUT_SECONDS)
            log(f"Attempt {attempt}: HTTP {r.status_code}")
            return
        except requests.RequestException as e:
            log(f"Attempt {attempt}: eroare - {e}")
            time.sleep(10)

while True:
    ping()
    time.sleep(INTERVAL_SECONDS)
