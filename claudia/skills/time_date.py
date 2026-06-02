from datetime import datetime

try:
    import pytz
    _HAS_PYTZ = True
except ImportError:
    _HAS_PYTZ = False

from skills import Skill


class TimeDateSkill(Skill):
    name = "time_date"
    triggers = ["what time", "what day", "current time", "what date", "what's the time", "what is the time", "what's today", "what day is it", "what's the date"]
    description = "Returns the current time, date, and day of the week in Jakarta timezone."

    def __init__(self, config: dict):
        self.timezone = config.get("user", {}).get("timezone", "Asia/Jakarta")

    def execute(self, params: dict) -> str:
        now = self._now()
        user_input = params.get("raw_input", "").lower()
        if "time" in user_input and "date" not in user_input and "day" not in user_input:
            return f"It's {now.strftime('%H:%M')} in Jakarta."
        if "date" in user_input:
            return f"Today is {now.strftime('%A, %d %B %Y')}."
        return f"It's {now.strftime('%H:%M')} on {now.strftime('%A, %d %B %Y')}."

    def _now(self) -> datetime:
        if _HAS_PYTZ:
            tz = pytz.timezone(self.timezone)
            return datetime.now(tz)
        from datetime import timezone, timedelta
        utc_plus7 = timezone(timedelta(hours=7))
        return datetime.now(utc_plus7)


if __name__ == "__main__":
    skill = TimeDateSkill({"user": {"timezone": "Asia/Jakarta"}})
    print(skill.execute({"raw_input": "What time is it?"}))
    print(skill.execute({"raw_input": "What's today's date?"}))
