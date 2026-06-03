import logging
import os
import subprocess
from pathlib import Path

from skills import Skill

logger = logging.getLogger(__name__)

SEARCH_ROOTS = [
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
]

MAX_RESULTS = 5


class FileManagerSkill(Skill):
    name = "file_manager"
    triggers = ["find file", "open document", "search files", "find document", "open file", "where is my file", "find my file"]
    description = "Searches for files by name and opens them on Windows."

    def __init__(self, config: dict):
        pass

    def execute(self, params: dict) -> str:
        raw = params.get("raw_input", "")
        filename = self._extract_filename(raw)
        if not filename:
            return "Which file would you like me to find?"

        matches = self._search(filename)
        if not matches:
            return f"No files matching '{filename}' found in common locations."

        if len(matches) == 1:
            self._open(matches[0])
            return f"Opening {matches[0].name}."

        # Multiple matches — open the most recently modified one
        best = max(matches, key=lambda p: p.stat().st_mtime)
        self._open(best)
        names = [m.name for m in matches[:MAX_RESULTS]]
        return f"Found {len(matches)} matches. Opening most recent: {best.name}. Others: {', '.join(names[1:])}."

    def _search(self, query: str) -> list[Path]:
        query_lower = query.lower()
        results: list[Path] = []
        for root in SEARCH_ROOTS:
            if not root.exists():
                continue
            try:
                for dirpath, _, files in os.walk(str(root)):
                    for fname in files:
                        if query_lower in fname.lower():
                            results.append(Path(dirpath) / fname)
                        if len(results) >= MAX_RESULTS * 3:
                            return results[:MAX_RESULTS]
            except PermissionError:
                continue
        return results[:MAX_RESULTS]

    def _open(self, path: Path) -> None:
        try:
            os.startfile(str(path))
        except Exception as e:
            logger.error("Could not open %s: %s", path, e)
            try:
                subprocess.Popen(["explorer", str(path.parent)])
            except Exception:
                pass

    def _extract_filename(self, text: str) -> str:
        lower = text.lower()
        for keyword in ("find file", "open document", "search files", "find document",
                        "open file", "where is", "find", "open", "search"):
            if keyword in lower:
                idx = lower.index(keyword) + len(keyword)
                return text[idx:].strip().rstrip("?.,!")
        return text.strip()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    skill = FileManagerSkill({})
    print(skill.execute({"raw_input": "find file resume"}))
