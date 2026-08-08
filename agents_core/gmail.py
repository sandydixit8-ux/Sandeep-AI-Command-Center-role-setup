"""Gmail integration using stdlib IMAP/SMTP (no SDK dependencies).

Configure via env:
    AGENT_GMAIL_USER        (your gmail address)
    AGENT_GMAIL_APP_PASSWORD  (Google app password — NOT your normal login password)

Reading uses IMAP; sending uses SMTP. Sending is gated: agents are instructed to get
explicit approval before calling send_email.
"""
from __future__ import annotations

import email
import imaplib
import smtplib
from email.header import decode_header
from email.message import EmailMessage
from pathlib import Path

from .config import OUTPUTS_DIR, get_settings
from .tools import ToolError, _strip_html


def _creds() -> tuple[str, str]:
    cfg = get_settings()
    user = cfg.gmail_user
    pwd = cfg.gmail_app_password
    if not user or not pwd:
        raise ToolError(
            "Gmail is not configured. Set AGENT_GMAIL_USER and AGENT_GMAIL_APP_PASSWORD "
            "in Agents/.env (create an app password at https://myaccount.google.com/apppasswords)."
        )
    return user, pwd


def _decode(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _body_text(msg: email.message.Message, max_chars: int = 5000) -> str:
    """Extract the plain-text body of an email message.

    Falls back to stripping an HTML body when no plain-text part exists (common
    with marketing/alert emails), so agents don't read raw HTML.
    """
    if msg.is_multipart():
        html_parts: list[str] = []
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            if ct == "text/plain":
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")[:max_chars]
            if ct == "text/html":
                html_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        if html_parts:
            return _strip_html(" ".join(html_parts), max_chars)
        return "(no plain-text body)"
    payload = msg.get_payload(decode=True)
    if payload:
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            return _strip_html(text, max_chars)
        return text[:max_chars]
    return "(no body)"


def _connect_imap() -> imaplib.IMAP4_SSL:
    user, pwd = _creds()
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com")
        conn.login(user, pwd)
        conn.select("INBOX")
        return conn
    except imaplib.IMAP4.error as exc:
        raise ToolError(f"IMAP login failed: {exc}") from exc


_IMAP_KEYWORDS = {
    "ALL", "ANSWERED", "DELETED", "DRAFT", "FLAGGED", "NEW", "OLD", "RECENT",
    "SEEN", "UNANSWERED", "UNDELETED", "UNDRAFT", "UNFLAGGED", "UNSEEN",
}


def _search(conn: imaplib.IMAP4_SSL, query: str, limit: int) -> list[bytes]:
    """Run a search, returning message ids (most recent first).

    Gmail-style queries ('from:x subject:y is:unread') go through the X-GM-RAW
    extension so the model's natural search syntax works; plain IMAP keywords
    (UNSEEN, ALL, ...) use the standard SEARCH command.
    """
    q = (query or "UNSEEN").strip()
    try:
        if q.upper() in _IMAP_KEYWORDS:
            status, data = conn.search(None, q)
        else:
            raw = q.replace('"', "'")
            status, data = conn.search(None, "X-GM-RAW", f'"{raw}"')
    except imaplib.IMAP4.error as exc:
        raise ToolError(f"gmail search failed for {q!r}: {exc}") from exc
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()[-int(limit):]


def _valid_id(message_id: str) -> bool:
    """IMAP message ids are plain integers; reject placeholders like '<id>'."""
    return bool(message_id) and message_id.strip().isdigit()


def _require_id(message_id: str) -> None:
    if not _valid_id(message_id):
        raise ToolError(
            f"invalid message id: {message_id!r}. Use the numeric id from gmail_inbox output (e.g. '4948')."
        )


def inbox_list(query: str = "UNSEEN", limit: int = 10) -> str:
    """Return a summary of the most recent messages matching a Gmail search query."""
    conn = _connect_imap()
    try:
        ids = _search(conn, query, limit)
        rows = []
        for num in ids:
            status, msg_data = conn.fetch(num, "(BODY.PEEK[HEADER])")
            if status != "OK" or not msg_data:
                continue
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            msg_id = msg.get("Message-ID", num.decode())
            rows.append(
                f"id={num.decode()}  from={_decode(msg.get('From', ''))}  date={msg.get('Date', '')}\n"
                f"  subject: {_decode(msg.get('Subject', ''))}\n"
                f"  msgid: {msg_id}"
            )
        return "\n\n".join(rows) if rows else "(no messages matched the query)"
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def read_thread(message_id: str, limit: int = 10) -> str:
    """Read the full body of a message by its IMAP id (use the id from inbox_list)."""
    _require_id(message_id)
    conn = _connect_imap()
    try:
        status, data = conn.fetch(message_id.strip(), "(BODY.PEEK[])")
        if status != "OK" or not data:
            return f"message not found: {message_id}"
        raw = data[0][1] if isinstance(data[0], tuple) else None
        if not raw:
            return "unable to read message"
        msg = email.message_from_bytes(raw)
        return (
            f"from: {_decode(msg.get('From', ''))}\n"
            f"to: {_decode(msg.get('To', ''))}\n"
            f"date: {msg.get('Date', '')}\n"
            f"subject: {_decode(msg.get('Subject', ''))}\n\n"
            f"{_body_text(msg)}"
        )
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def draft_reply(message_id: str, reply_body: str) -> str:
    """Save a reply draft for a message as a text file for review before sending."""
    _require_id(message_id)
    conn = _connect_imap()
    try:
        status, data = conn.fetch(message_id.strip(), "(BODY.PEEK[HEADER])")
        if status != "OK" or not data:
            return f"message not found: {message_id}"
        raw = data[0][1] if isinstance(data[0], tuple) else None
        msg = email.message_from_bytes(raw) if raw else None
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass

    to = _decode(msg.get("Reply-To", "")) if msg else ""
    if not to:
        to = _decode(msg.get("From", "")) if msg else ""
    subject = _decode(msg.get("Subject", "")) if msg else ""
    ref_subject = (subject or "Re: ").strip()
    if not ref_subject.lower().startswith("re:"):
        ref_subject = f"Re: {ref_subject}"

    out_dir = OUTPUTS_DIR / "gmail"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"draft-{message_id}.txt"
    content = (
        f"TO: {to}\nSUBJECT: {ref_subject}\n\n{reply_body}\n\n"
        f"-- draft for review. To send: python -m agents_core.gmail --send {message_id}\n"
    )
    out_path.write_text(content, encoding="utf-8")
    return f"draft saved to {out_path}\n{content}"


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail SMTP. Use only after explicit user approval."""
    user, pwd = _creds()
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(user, pwd)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise ToolError(f"SMTP auth failed (check app password): {exc}") from exc
    except smtplib.SMTPException as exc:
        raise ToolError(f"SMTP error: {exc}") from exc
    return f"sent to {to}: {subject}"


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Gmail helpers")
    parser.add_argument("--send", help="send a draft file (TO/SUBJECT/body format)")
    parser.add_argument("--inbox", action="store_true", help="list recent unread mail")
    args = parser.parse_args()
    if args.send:
        content = Path(args.send).read_text(encoding="utf-8")
        lines = content.splitlines()
        to = subject = ""
        for line in lines:
            if line.upper().startswith("TO:"):
                to = line[3:].strip()
            elif line.upper().startswith("SUBJECT:"):
                subject = line[8:].strip()
        body = "\n".join(lines)
        # strip the header block from the body
        if "TO:" in body and "SUBJECT:" in body:
            body = body.split("SUBJECT:", 1)[1].split("\n", 1)[1]
        print(send_email(to, subject, body.strip()))
    elif args.inbox:
        print(inbox_list())
    else:
        parser.error("use --send <draft-file> or --inbox")
        sys.exit(1)
