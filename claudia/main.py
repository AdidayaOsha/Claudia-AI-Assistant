import atexit
import logging
import os
import socket as _socket
import sys
import threading
from pathlib import Path

# Bootstrap: load .env and add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

_lock_socket = None


def _acquire_socket_lock() -> None:
    """Prevent multiple CLAUDIA instances via a loopback socket lock.

    Binding to 127.0.0.1:65432 succeeds only for the first instance; the OS
    releases the socket automatically when the process exits for any reason,
    so there are no stale lock files to clean up.
    """
    global _lock_socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 0)
    try:
        s.bind(("127.0.0.1", 65432))
    except OSError:
        print("[CLAUDIA] Already running — stop the existing instance first.")
        sys.exit(1)
    _lock_socket = s
    atexit.register(s.close)


_acquire_socket_lock()

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

import yaml


def _setup_logging(config: dict) -> None:
    log_dir = project_root / config.get("logging", {}).get("dir", "logs")
    log_dir.mkdir(exist_ok=True)
    from logging.handlers import TimedRotatingFileHandler
    log_file = log_dir / "claudia.log"
    level = getattr(logging, config.get("logging", {}).get("level", "INFO"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            TimedRotatingFileHandler(str(log_file), when="midnight", backupCount=14, encoding="utf-8"),
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

    # Research module status
    research_cfg = config.get("research", {})
    if research_cfg.get("enabled"):
        sources = research_cfg.get("sources", {})
        brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
        brave_status = "[OK]" if (sources.get("brave_search") and brave_key) else "[no key]"
        logger.info(f"[CLAUDIA] Research: DDG [OK] | Wikipedia [OK] | Brave {brave_status} | Cache TTL={research_cfg.get('cache_ttl_seconds', 300)}s [OK]")
    else:
        logger.info("[CLAUDIA] Research: disabled (research.enabled: false in config.yaml)")

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

    # Brain backend status
    brain_cfg = config.get("brain", {})
    active = brain_cfg.get("active_provider", "claude")
    claude_ok = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    local_ok = assistant.brain._local_backend.is_available()
    logger.info(
        "[CLAUDIA] Brain backend: %s active | Claude %s | Local/Ollama %s",
        active.upper(),
        "[OK]" if claude_ok else "[no key]",
        "[OK]" if local_ok else "[unreachable]",
    )

    # Wire dashboard socket emit + incoming text commands to assistant
    try:
        from ui.dashboard import get_socketio
        sio = get_socketio()
        if sio:
            assistant.set_socket_emit(lambda event, data: sio.emit(event, data))

            @sio.on("connect")
            def on_client_connect():
                sio.emit("provider_changed", assistant.brain.provider_info())

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
