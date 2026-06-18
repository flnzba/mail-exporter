"""
imap_utils.py — Low-level IMAP helpers.

All heavy I/O lives here so the Reflex state stays clean.
Only stdlib is used (imaplib, email, mailbox, zipfile).
"""

from __future__ import annotations

import imaplib
import re
from typing import Iterator


# ── Connection ────────────────────────────────────────────────────────────────

def connect_imap(
    host: str,
    port: int,
    username: str,
    password: str,
    ssl: bool = True,
) -> imaplib.IMAP4:
    """Open and authenticate an IMAP connection."""
    if ssl:
        conn = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(username, password)
    return conn


# ── Folder listing ────────────────────────────────────────────────────────────

def list_folders(conn: imaplib.IMAP4) -> list[str]:
    """
    Return all folder names for the mailbox.

    IMAP LIST responses look like:
        (\\HasNoChildren) "." "INBOX"
        (\\HasNoChildren) "/" Sent
    We parse both '.' and '/' separators and strip surrounding quotes.
    """
    status, raw = conn.list()
    if status != "OK":
        return []

    folders: list[str] = []
    for item in raw:
        if item is None:
            continue
        decoded = item.decode("utf-8", errors="replace")

        # Match the folder name after the separator
        # e.g.:  (\HasNoChildren) "." "Sent Items"
        #        (\HasNoChildren) "/" INBOX
        match = re.search(
            r'\)\s+"[./]"\s+"?([^"]+)"?\s*$'   # quoted separator variant
            r'|\)\s+NIL\s+"?([^"]+)"?\s*$',     # NIL separator variant
            decoded,
        )
        if match:
            name = (match.group(1) or match.group(2) or "").strip().strip('"')
        else:
            # Fallback: take the last token
            parts = decoded.rsplit(" ", 1)
            name = parts[-1].strip().strip('"') if parts else ""

        if name:
            folders.append(name)

    return sorted(set(folders))


# ── Message count ─────────────────────────────────────────────────────────────

def count_messages_in_folder(conn: imaplib.IMAP4, folder: str) -> int:
    """Return the number of messages in a folder (0 on error)."""
    try:
        status, data = conn.select(f'"{folder}"', readonly=True)
        if status == "OK" and data and data[0]:
            return int(data[0].decode())
        return 0
    except Exception:
        return 0


# ── Message fetching ──────────────────────────────────────────────────────────

def fetch_raw_messages(conn: imaplib.IMAP4, folder: str) -> Iterator[bytes]:
    """
    Yield the raw RFC 822 bytes for every message in *folder*.

    Silently skips messages that fail to download.
    """
    try:
        status, data = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            return
        _, search_data = conn.search(None, "ALL")
        if not search_data or not search_data[0]:
            return
        msg_ids = search_data[0].split()
    except Exception:
        return

    for uid in msg_ids:
        try:
            _, msg_data = conn.fetch(uid, "(RFC822)")
            if msg_data and msg_data[0]:
                raw = msg_data[0][1]
                if isinstance(raw, bytes):
                    yield raw
        except Exception:
            continue
