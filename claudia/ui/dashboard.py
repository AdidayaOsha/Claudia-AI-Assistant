import logging
from datetime import datetime, timezone
from pathlib import Path

import psutil
from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

_socketio: SocketIO | None = None


def create_app(config: dict) -> tuple[Flask, SocketIO]:
    global _socketio
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )
    app.config["SECRET_KEY"] = "claudia-dashboard-secret"
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    _socketio = socketio

    @app.route("/")
    def index():
        return render_template("index.html", config=config)

    @app.route("/api/stats")
    def stats():
        boot_time = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
        uptime_seconds = int((datetime.now(timezone.utc) - boot_time).total_seconds())
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return jsonify({
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram_percent": psutil.virtual_memory().percent,
            "uptime": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            "timestamp": datetime.now().isoformat(),
        })

    @socketio.on("connect")
    def on_connect():
        logger.debug("Dashboard client connected")
        socketio.emit("status", {"message": "Connected to CLAUDIA"})

    @socketio.on("disconnect")
    def on_disconnect():
        logger.debug("Dashboard client disconnected")

    @socketio.on("user_input")
    def on_user_input(data):
        """Allow sending commands from the dashboard UI."""
        text = data.get("text", "").strip()
        if text:
            socketio.emit("transcript", {"role": "user", "text": text})

    return app, socketio


def get_socketio() -> SocketIO | None:
    return _socketio


if __name__ == "__main__":
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    app, sio = create_app(cfg)
    sio.run(app, host="127.0.0.1", port=5000, debug=True)
