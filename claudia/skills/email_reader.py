import email
import imaplib
import logging
import os

from skills import Skill

logger = logging.getLogger(__name__)

GMAIL_IMAP = "imap.gmail.com"


class EmailReaderSkill(Skill):
    name = "email_reader"
    triggers = ["email", "inbox", "messages", "mail", "unread", "check email"]
    description = "Reads and summarizes Gmail inbox via IMAP."

    def __init__(self, config: dict):
        self.enabled: bool = config.get("features", {}).get("enable_email", False)
        self.email_address: str = os.environ.get("GMAIL_ADDRESS", "")
        self.app_password: str = os.environ.get("GMAIL_APP_PASSWORD", "")
        self.max_emails: int = 5

    def execute(self, params: dict) -> str:
        if not self.enabled:
            return "Email is disabled. Set enable_email: true in config.yaml."
        if not self.email_address or not self.app_password:
            return "Gmail credentials not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env."
        try:
            summaries = self._fetch_unread()
            if not summaries:
                return "Inbox is clear. No unread messages."
            return f"{len(summaries)} unread: " + " | ".join(summaries)
        except imaplib.IMAP4.error as e:
            logger.error("IMAP error: %s", e)
            return "Email access failed. Check your Gmail app password."
        except Exception as e:
            logger.error("Email error: %s", e)
            return "Could not access email right now."

    def _fetch_unread(self) -> list[str]:
        with imaplib.IMAP4_SSL(GMAIL_IMAP) as mail:
            mail.login(self.email_address, self.app_password)
            mail.select("inbox")
            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split()
            if not ids:
                return []
            summaries = []
            for uid in ids[-self.max_emails:]:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                sender = msg.get("From", "Unknown")
                subject = msg.get("Subject", "(no subject)")
                short_sender = sender.split("<")[0].strip().strip('"') or sender
                summaries.append(f"From {short_sender}: {subject}")
            return summaries


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    cfg = {"features": {"enable_email": True}}
    skill = EmailReaderSkill(cfg)
    print(skill.execute({}))
