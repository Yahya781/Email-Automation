"""Google Sheets + Gmail access. Gmail use is limited to creating/reading drafts and reading threads.
There is deliberately no send call anywhere in this project."""
import base64
import os
from email.message import EmailMessage

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.compose",   # create drafts
    "https://www.googleapis.com/auth/gmail.readonly",  # read threads and bounces
]


def credentials() -> Credentials:
    return Credentials(
        None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def col_letter(i: int) -> str:  # 0 -> A
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


class Queue:
    def __init__(self, creds, sheet_id: str, tab: str):
        self.svc = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
        self.sheet_id, self.tab = sheet_id, tab
        self.header: list[str] = []

    def load(self) -> list[dict]:
        vals = self.svc.values().get(spreadsheetId=self.sheet_id, range=f"'{self.tab}'!A1:Z500").execute().get("values", [])
        if not vals:
            return []
        self.header = vals[0]
        rows = []
        for n, r in enumerate(vals[1:], start=2):
            r = r + [""] * (len(self.header) - len(r))
            d = {}
            for k, v in zip(self.header, r):  # duplicate header names: the first column wins
                d.setdefault(k, v)
            d["_row"] = n
            if d.get("Company", "").strip():
                rows.append(d)
        return rows

    def update(self, row: int, **fields):
        """fields keys are header names. RAW input so model text can never become a formula."""
        data = []
        for name, value in fields.items():
            if name not in self.header:
                raise KeyError(f"no column called {name}")
            data.append({"range": f"'{self.tab}'!{col_letter(self.header.index(name))}{row}", "values": [[str(value)]]})
        if data:
            self.svc.values().batchUpdate(
                spreadsheetId=self.sheet_id, body={"valueInputOption": "RAW", "data": data}).execute()


    def append(self, rows: list[dict]):
        """Add new rows at the bottom. Each dict's keys are header names; columns it leaves out stay empty."""
        values = []
        for row in rows:
            for name in row:
                if name not in self.header:
                    raise KeyError(f"no column called {name}")
            values.append([str(row.get(h, "")) for h in self.header])
        if values:
            self.svc.values().append(
                spreadsheetId=self.sheet_id, range=f"'{self.tab}'!A1", valueInputOption="RAW",
                insertDataOption="INSERT_ROWS", body={"values": values}).execute()


class Mail:
    def __init__(self, creds):
        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False).users()
        self.me = self.svc.getProfile(userId="me").execute()["emailAddress"].lower()

    def create_draft(self, to, subject, body, thread_id=None, in_reply_to=None):
        m = EmailMessage()
        m["To"], m["Subject"] = to, subject
        if in_reply_to:
            m["In-Reply-To"] = m["References"] = in_reply_to
        m.set_content(body)
        msg = {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}
        if thread_id:
            msg["threadId"] = thread_id
        d = self.svc.drafts().create(userId="me", body={"message": msg}).execute()
        return d["id"], d["message"]["threadId"]

    def draft_exists(self, draft_id: str) -> bool:
        try:
            self.svc.drafts().get(userId="me", id=draft_id, format="minimal").execute()
            return True
        except HttpError as e:
            if e.resp.status == 404:
                return False
            raise

    def thread(self, thread_id: str) -> list[dict]:
        t = self.svc.threads().get(userId="me", id=thread_id, format="metadata",
                                   metadataHeaders=["From", "Message-ID", "Subject"]).execute()
        out = []
        for m in t.get("messages", []):
            h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
            out.append({"from": h.get("from", "").lower(), "message_id": h.get("message-id", ""),
                        "subject": h.get("subject", ""), "labels": m.get("labelIds", [])})
        return out

    def has_reply(self, messages: list[dict]) -> bool:
        return any("SENT" not in m["labels"] and "DRAFT" not in m["labels"] and self.me not in m["from"]
                   for m in messages)

    @staticmethod
    def sent_count(messages: list[dict]) -> int:
        return sum(1 for m in messages if "SENT" in m["labels"])

    def bounced(self, address: str) -> bool:
        q = f'from:(mailer-daemon OR postmaster) "{address}" newer_than:60d'
        return bool(self.svc.messages().list(userId="me", q=q, maxResults=1).execute().get("messages"))
