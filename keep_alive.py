from threading import Thread
from flask import Flask
import os

app = Flask(__name__)

@app.get("/")
def home():
    return "OK"

def keep_alive():
    port = int(os.getenv("PORT", "8080"))
    Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=port,
            use_reloader=False
        ),
        daemon=True
    ).start()
