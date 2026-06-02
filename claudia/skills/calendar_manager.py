import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from skills import Skill

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarManagerSkill(Skill):
    name = "calendar_manager"
    triggers = ["schedule", "calendar", "reminder", "meeting", "appointment", "event", "what do I have"]
    description = "Reads and writes Google Calendar events."

    def __init__(self, config: dict):
        self.enabled: bool = config.get("features", {}).get("enable_calendar", False)
        self.credentials_path = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"))
        self.token_path = Path("token_calendar.json")
        self._service = None

    def execute(self, params: dict) -> str:
        if not self.enabled:
            return "Calendar is disabled. Set enable_calendar: true in config.yaml and add credentials.json."
        try:
            service = self._get_service()
            raw = params.get("raw_input", "").lower()
            if any(w in raw for w in ("what do i have", "upcoming", "today", "schedule")):
                return self._list_upcoming(service)
            return "I can show your upcoming events. Try 'what do I have today'."
        except Exception as e:
            logger.error("Calendar error: %s", e)
            return "Calendar access failed. Check your credentials."

    def _get_service(self):
        if self._service:
            return self._service
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json())

        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def _list_upcoming(self, service, max_events: int = 5) -> str:
        now = datetime.utcnow().isoformat() + "Z"
        result = service.events().list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_events,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return "No upcoming events found."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            summary = e.get("summary", "Untitled")
            lines.append(f"{start[:16].replace('T', ' ')}: {summary}")
        return "Upcoming events: " + "; ".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cfg = {"features": {"enable_calendar": True}}
    skill = CalendarManagerSkill(cfg)
    print(skill.execute({"raw_input": "what do I have today"}))
