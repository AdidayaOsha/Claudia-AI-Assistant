import logging
import os
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path

from skills import Skill

logger = logging.getLogger(__name__)

# Built-in name → executable/command map (Windows)
APP_MAP: dict[str, str] = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "notepad": "notepad",
    "calculator": "calc",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "explorer": "explorer",
    "file explorer": "explorer",
    "terminal": "wt",
    "windows terminal": "wt",
    "powershell": "powershell",
    "command prompt": "cmd",
    "cmd": "cmd",
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "spotify": "spotify",
    "discord": "discord",
    "slack": "slack",
    "zoom": "zoom",
    "paint": "mspaint",
    "task manager": "taskmgr",
    "snipping tool": "snippingtool",
}

BROWSER_SEARCH_KEYWORDS = (
    "browse to",
    "open browser and search",
    "search in chrome",
    "search in browser",
    "open google and search",
    "google search for",
)


class AppLauncherSkill(Skill):
    name = "app_launcher"
    triggers = [
        "open app", "open chrome", "open firefox", "open edge", "open notepad",
        "open spotify", "open discord", "launch app", "launch chrome", "start app",
        "open powershell", "open cmd", "open terminal", "open command prompt",
        "open teams", "open microsoft teams", "open service studio", "open outsystems",
        "open the app", "open the browser", "browse to", "open browser",
        "open word", "open excel", "open outlook", "open vs code", "open vscode",
        "open paint", "open task manager", "launch teams", "launch service studio",
        "start powershell", "start cmd", "start terminal",
    ]
    description = "Opens applications, URLs, and browser searches on Windows."

    def __init__(self, config: dict):
        # User-defined apps from config.yaml — takes priority over built-in APP_MAP
        self._user_apps: dict[str, str] = {
            k.lower(): v for k, v in config.get("apps", {}).items()
        }

    def execute(self, params: dict) -> str:
        raw = params.get("raw_input", "")

        # Browser search check first (before target extraction)
        if self._is_browser_search(raw):
            return self._browser_search(raw)

        target = self._extract_target(raw)
        if not target:
            return "What would you like me to open?"

        # Direct URL
        if target.startswith(("http://", "https://", "www.")):
            return self._open_url(target)

        target_key = target.lower()

        # User-configured apps (config.yaml) — highest priority
        if target_key in self._user_apps:
            return self._launch(self._user_apps[target_key], target)

        # Built-in APP_MAP
        if target_key in APP_MAP:
            return self._launch(APP_MAP[target_key], target)

        # Windows shell fallback: `start ""` lets Windows find the app via Start Menu / PATH
        return self._shell_start(target)

    # ------------------------------------------------------------------ #
    #  Launch helpers                                                       #
    # ------------------------------------------------------------------ #

    def _launch(self, executable: str, display_name: str) -> str:
        """Launch by executable name, URI (e.g. msteam:), or full path."""
        try:
            if executable.endswith(":"):
                os.startfile(executable)
            elif Path(executable).is_absolute() and Path(executable).exists():
                os.startfile(executable)
            else:
                os.startfile(executable)
            return f"{display_name.title()} launched."
        except Exception as e:
            logger.error("Launch failed (%s): %s", executable, e)
            return f"Couldn't launch {display_name}."

    def _open_url(self, url: str) -> str:
        try:
            os.startfile(url if url.startswith("http") else f"https://{url}")
            return f"Opening {url}."
        except Exception as e:
            logger.error("URL open failed: %s", e)
            return f"Couldn't open {url}."

    def _shell_start(self, target: str) -> str:
        """Last resort: hand the name to Windows Shell via os.startfile."""
        try:
            os.startfile(target)
            return f"Opening {target}."
        except Exception as e:
            logger.error("Shell start failed (%s): %s", target, e)
            return (
                f"I couldn't find '{target}'. "
                "You can add it to the apps section in config.yaml."
            )

    # ------------------------------------------------------------------ #
    #  Browser search                                                       #
    # ------------------------------------------------------------------ #

    def _is_browser_search(self, raw: str) -> bool:
        lower = raw.lower()
        return any(kw in lower for kw in BROWSER_SEARCH_KEYWORDS)

    def _browser_search(self, raw: str) -> str:
        query = self._extract_search_query(raw)
        if not query:
            return "What would you like me to search for?"
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        webbrowser.open(url)
        return f"Searching Google for '{query}'."

    def _extract_search_query(self, text: str) -> str:
        lower = text.lower()
        for kw in sorted(BROWSER_SEARCH_KEYWORDS, key=len, reverse=True):
            if kw in lower:
                idx = lower.index(kw) + len(kw)
                # strip filler words like "for", "about"
                remainder = text[idx:].strip()
                for filler in ("for ", "about "):
                    if remainder.lower().startswith(filler):
                        remainder = remainder[len(filler):]
                return remainder.rstrip("?.,!")
        return text.strip()

    # ------------------------------------------------------------------ #
    #  Target extraction                                                    #
    # ------------------------------------------------------------------ #

    def _extract_target(self, text: str) -> str:
        lower = text.lower()
        for keyword in ("open", "launch", "start", "run"):
            if keyword in lower:
                idx = lower.index(keyword) + len(keyword)
                return text[idx:].strip().rstrip("?.,!")
        return text.strip()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    skill = AppLauncherSkill({})
    print(skill.execute({"raw_input": "open notepad"}))
    print(skill.execute({"raw_input": "browse to latest Python news"}))
