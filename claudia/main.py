import atexit
import logging
import os
import sys
import threading
from pathlib import Path

# Bootstrap: load .env and add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

PID_FILE = project_root / "claudia.pid"


def _acquire_lock() -> None:
    """Prevent multiple CLAUDIA instances from running simultaneously."""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            import psutil
            if psutil.pid_exists(old_pid):
                print(f"[CLAUDIA] Already running (PID {old_pid}). Stop the existing instance first.")
                sys.exit(1)
        except (ValueError, ImportError):
            pass  # stale or unreadable — overwrite
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))


_acquire_lock()

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

import yaml


def _setup_logging(config: dict) -> None:
    log_dir = project_root / config.get("logging", {}).get("dir", "logs")
    log_dir.mkdir(exist_ok=True)
    from datetime import datetime
    log_file = log_dir / f"claudia_{datetime.now().strftime('%Y%m%d')}.log"
    level = getattr(logging, config.get("logging", {}).get("level", "INFO"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _load_config() -> dict:
    config_path = project_root / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _print_status(msg: str, ok: bool = True) -> None:
    icon = "[OK]" if ok else "[!!]"
    print(f"[CLAUDIA] {msg} {icon}")


def main() -> None:
    print("[CLAUDIA] Initializing systems...")

    config = _load_config()
    _setup_logging(config)
    logger = logging.getLogger("main")

    # Memory
    from core.memory import Memory
    mem_file = project_root / config.get("memory", {}).get("file", "memory.json")
    memory = Memory(str(mem_file))
    _print_status(f"Loading memory... ({memory.entry_count} entries)")

    # Anthropic connection check
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        _print_status("Connecting to Anthropic Claude...")
    else:
        _print_status("ANTHROPIC_API_KEY not set — LLM fallback only", ok=False)

    # Skills
    from skills import load_all_skills
    skills = load_all_skills(config)
    _print_status(f"Skill modules loaded: {len(skills)} skills")

    # Dashboard (background thread)
    dashboard_thread = None
    try:
        from ui.dashboard import create_app
        flask_app, socketio = create_app(config)
        port = config.get("dashboard", {}).get("port", 5000)
        host = config.get("dashboard", {}).get("host", "127.0.0.1")
        dashboard_thread = threading.Thread(
            target=lambda: socketio.run(flask_app, host=host, port=port, use_reloader=False, allow_unsafe_werkzeug=True),
            daemon=True,
            name="Dashboard",
        )
        dashboard_thread.start()
        _print_status(f"Dashboard running at http://{host}:{port}")
    except Exception as e:
        logger.warning("Dashboard unavailable: %s", e)
        _print_status(f"Dashboard unavailable: {e}", ok=False)

    # Assistant
    from core.assistant import Assistant
    assistant = Assistant(config)

    # Wire dashboard socket emit + incoming text commands to assistant
    try:
        from ui.dashboard import get_socketio
        sio = get_socketio()
        if sio:
            assistant.set_socket_emit(lambda event, data: sio.emit(event, data))

            @sio.on("user_input")
            def on_dashboard_input(data):
                text = data.get("text", "").strip()
                if text:
                    assistant.submit_text(text)
    except Exception as e:
        logger.warning("Could not wire dashboard input: %s", e)

    # Microphone calibration info
    if assistant.listener.is_text_mode:
        _print_status("Microphone unavailable — text input mode active", ok=False)
    else:
        _print_status("Microphone calibrated")

    print("[CLAUDIA] All systems operational.")
    print()

    try:
        assistant.run()
    except KeyboardInterrupt:
        print("\n[CLAUDIA] Shutting down. Goodbye.")
        assistant.shutdown()


if __name__ == "__main__":
    main()
