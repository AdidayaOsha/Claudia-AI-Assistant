import logging
import subprocess
from datetime import datetime
from pathlib import Path

from skills import Skill

logger = logging.getLogger(__name__)


class SystemControlSkill(Skill):
    name = "system_control"
    triggers = ["volume up", "volume down", "mute", "unmute", "take a screenshot", "take screenshot", "read clipboard", "set volume", "increase volume", "decrease volume"]
    description = "Controls system volume, takes screenshots, and manages clipboard."

    def __init__(self, config: dict):
        self.screenshot_dir = Path.home() / "Pictures" / "Claudia Screenshots"

    def execute(self, params: dict) -> str:
        raw = params.get("raw_input", "").lower()
        if "screenshot" in raw:
            return self._take_screenshot()
        if "clipboard" in raw or "copy" in raw or "paste" in raw:
            return self._clipboard_action(raw)
        if "mute" in raw:
            return self._mute()
        if "unmute" in raw:
            return self._unmute()
        if "volume" in raw or "louder" in raw or "quieter" in raw:
            return self._volume_action(raw)
        return "What system action would you like?"

    def _take_screenshot(self) -> str:
        try:
            import pyautogui
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            filename = self.screenshot_dir / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            screenshot = pyautogui.screenshot()
            screenshot.save(str(filename))
            return f"Screenshot saved to {filename.name}."
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return "Screenshot failed."

    def _clipboard_action(self, raw: str) -> str:
        try:
            import pyperclip
            if "copy" in raw:
                content = raw.split("copy")[-1].strip().strip('"\'')
                if content:
                    pyperclip.copy(content)
                    return f"Copied to clipboard."
                return "What would you like me to copy?"
            if "paste" in raw or "clipboard" in raw:
                content = pyperclip.paste()
                return f"Clipboard contains: {content[:100]}{'…' if len(content) > 100 else ''}"
        except Exception as e:
            logger.error("Clipboard error: %s", e)
            return "Clipboard action failed."

    def _volume_action(self, raw: str) -> str:
        try:
            import pyautogui
            if "up" in raw or "louder" in raw or "increase" in raw:
                for _ in range(5):
                    pyautogui.press("volumeup")
                return "Volume increased."
            if "down" in raw or "quieter" in raw or "decrease" in raw or "lower" in raw:
                for _ in range(5):
                    pyautogui.press("volumedown")
                return "Volume decreased."
            level = self._extract_number(raw)
            if level is not None:
                # Windows: use nircmd if available, else VK_VOLUME_UP/DOWN approximation
                self._set_volume_nircmd(level)
                return f"Volume set to {level}%."
            return "Say 'volume up', 'volume down', or 'volume 50'."
        except Exception as e:
            logger.error("Volume control error: %s", e)
            return "Volume control unavailable."

    def _mute(self) -> str:
        try:
            import pyautogui
            pyautogui.press("volumemute")
            return "Muted."
        except Exception as e:
            logger.error("Mute error: %s", e)
            return "Mute failed."

    def _unmute(self) -> str:
        try:
            import pyautogui
            pyautogui.press("volumemute")
            return "Unmuted."
        except Exception as e:
            logger.error("Unmute error: %s", e)
            return "Unmute failed."

    def _set_volume_nircmd(self, level: int) -> None:
        val = int(level / 100 * 65535)
        subprocess.run(["nircmd", "setsysvolume", str(val)], check=False)

    def _extract_number(self, text: str) -> int | None:
        import re
        match = re.search(r"\b(\d{1,3})\b", text)
        if match:
            n = int(match.group(1))
            return max(0, min(100, n))
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    skill = SystemControlSkill({})
    print(skill.execute({"raw_input": "take a screenshot"}))
