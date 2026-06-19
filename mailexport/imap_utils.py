"""
imap_utils.py — Low-level IMAP helpers.

All heavy I/O lives here so the Reflex state stays clean.
Only stdlib is used (imaplib, email, mailbox, zipfile).
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import mailbox
import re
import zipfile
from typing import Iterator

# Custom headers used to embed source-folder + IMAP-flag metadata into exported
# messages so an import can losslessly reconstruct folders and read/unread state.
# They are ignored by every mail client (any "X-" header is), so exports stay valid.
META_FOLDER = "X-Mailexport-Folder"
META_FLAGS = "X-Mailexport-Flags"


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


def fetch_messages_with_flags(
    conn: imaplib.IMAP4, folder: str
) -> Iterator[tuple[bytes, list[str]]]:
    """
    Yield ``(raw_rfc822_bytes, imap_flags)`` for every message in *folder*.

    Like :func:`fetch_raw_messages` but also returns the IMAP flag set
    (e.g. ``['\\\\Seen', '\\\\Flagged']``) so an export can record read/unread
    state. Silently skips messages that fail to download.
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
            _, msg_data = conn.fetch(uid, "(RFC822 FLAGS)")
        except Exception:
            continue
        if not msg_data:
            continue

        raw: bytes | None = None
        flag_tokens: tuple = ()
        for part in msg_data:
            if isinstance(part, tuple) and len(part) >= 2:
                meta, body = part[0], part[1]
                if isinstance(body, (bytes, bytearray)):
                    raw = bytes(body)
                if isinstance(meta, (bytes, bytearray)) and b"FLAGS" in meta.upper():
                    flag_tokens = imaplib.ParseFlags(bytes(meta))
            elif isinstance(part, (bytes, bytearray)) and b"FLAGS" in part.upper():
                flag_tokens = imaplib.ParseFlags(bytes(part))

        if raw is None:
            continue
        flags = [
            t.decode("ascii", "replace") if isinstance(t, (bytes, bytearray)) else str(t)
            for t in flag_tokens
        ]
        yield raw, flags


# ── Import: folder creation + APPEND ───────────────────────────────────────────

def ensure_folder(conn: imaplib.IMAP4, folder: str) -> None:
    """
    Create *folder* on the server if it does not already exist, and subscribe to
    it so webmail clients (Roundcube etc.) actually display it.

    CREATE on an existing mailbox raises on many servers; we swallow all errors
    because a genuine problem will surface on the subsequent APPEND, and several
    servers auto-create the mailbox on APPEND anyway.
    """
    try:
        conn.create(f'"{folder}"')
    except Exception:
        pass
    try:
        conn.subscribe(f'"{folder}"')
    except Exception:
        pass


def get_namespace_info(conn: imaplib.IMAP4) -> tuple[str, str]:
    """
    Return ``(personal_namespace_prefix, hierarchy_separator)`` for the account.

    On a typical Froxlor / Dovecot Maildir++ server this is ``("INBOX.", ".")``;
    on others it may be ``("", "/")``. Falls back gracefully when the server does
    not support the NAMESPACE extension.
    """
    prefix, sep = "", "/"
    # Hierarchy separator from LIST "" "" (supported everywhere).
    try:
        status, data = conn.list("", "")
        if status == "OK" and data and data[0]:
            quoted = re.findall(rb'"([^"]*)"', data[0])
            if quoted and quoted[0]:
                sep = quoted[0].decode("ascii", "replace")
    except Exception:
        pass
    # Personal namespace prefix from NAMESPACE (RFC 2342), e.g. (("INBOX." ".")).
    try:
        status, data = conn.namespace()
        if status == "OK" and data and data[0]:
            m = re.search(rb'\(\("([^"]*)"\s+"([^"]*)"', data[0])
            if m:
                prefix = m.group(1).decode("ascii", "replace")
                if m.group(2):
                    sep = m.group(2).decode("ascii", "replace")
    except Exception:
        pass
    return prefix, sep


def map_folder_name(name: str, prefix: str = "", sep: str = "/") -> str:
    """
    Map a source folder name onto the destination server's namespace.

    - INBOX always stays "INBOX" (never prefixed).
    - A leading source "INBOX" namespace is stripped (so "INBOX.Sent" → "Sent").
    - '/' and '\\' are treated as universal hierarchy separators and rebuilt with
      *sep* (a literal '.' in a name is left alone — it may not be a separator).
    - *prefix* (e.g. "INBOX.") is prepended unless already present.
    """
    name = name.strip().strip('"')
    if not name or name.upper() == "INBOX":
        return "INBOX"
    low = name.upper()
    for s in ("/", ".", "\\"):
        if low.startswith("INBOX" + s):
            name = name[len("INBOX" + s):]
            break
    parts = [p for p in re.split(r"[\\/]", name) if p]
    mapped = sep.join(parts) if parts else name
    if prefix and not mapped.upper().startswith(prefix.upper()):
        mapped = prefix + mapped
    return mapped


def _internal_date_from_raw(raw: bytes) -> str | None:
    """
    Best-effort IMAP INTERNALDATE (quoted string) from a message's ``Date:``
    header, or ``None`` when missing/unparseable (APPEND accepts ``None``).
    """
    try:
        msg = email.message_from_bytes(raw)
        date_hdr = msg.get("Date")
        if not date_hdr:
            return None
        dt = email.utils.parsedate_to_datetime(date_hdr)  # may raise ValueError
        if dt is None:
            return None
        return imaplib.Time2Internaldate(dt.timestamp())
    except Exception:
        return None


# Flags the server manages itself and that APPEND must not try to set.
_NON_SETTABLE_FLAGS = {r"\Recent"}


def _flags_to_imap_str(flags: list[str] | None) -> str | None:
    """Build a parenthesised IMAP flag string, or ``None`` for no flags."""
    if not flags:
        return None
    settable = [f for f in flags if f not in _NON_SETTABLE_FLAGS]
    if not settable:
        return None
    return "(" + " ".join(settable) + ")"


def append_message(
    conn: imaplib.IMAP4,
    folder: str,
    raw: bytes,
    flags: list[str] | None = None,
) -> tuple[bool, str]:
    """
    APPEND a single raw RFC822 message into *folder*.

    The internal date is derived from the message's ``Date:`` header (server-now
    when absent). Returns ``(ok, detail)`` — ``detail`` carries the server's reason
    on failure so the caller can surface it instead of silently counting failures.
    *raw* must already use CRLF line endings (imaplib does not normalise bytes
    payloads) — see :func:`_to_crlf`.
    """
    try:
        flag_str = _flags_to_imap_str(flags)
        internal_date = _internal_date_from_raw(raw)
        status, resp = conn.append(f'"{folder}"', flag_str, internal_date, raw)
        if status == "OK":
            return True, ""
        detail = resp[0].decode("utf-8", "replace") if resp and resp[0] else status
        return False, detail
    except Exception as exc:
        return False, str(exc)


# ── Import: reading exported .mbox / .zip sources ──────────────────────────────

def _to_crlf(data: bytes) -> bytes:
    """Normalise all line endings to CRLF, as required by IMAP APPEND."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")


def _strip_meta(raw: bytes) -> tuple[str | None, list[str], bytes]:
    """
    Read and remove the embedded ``X-Mailexport-*`` metadata headers.

    Returns ``(folder_or_None, flags_list, clean_crlf_bytes)``. ``folder``/``flags``
    are ``None``/``[]`` for plain messages that carry no metadata (old exports or
    third-party files), letting the caller fall back to sensible defaults.
    """
    try:
        msg = email.message_from_bytes(raw)
    except Exception:
        return None, [], _to_crlf(raw)

    folder = msg.get(META_FOLDER)
    flags_hdr = msg.get(META_FLAGS)
    flags = flags_hdr.split() if flags_hdr else []

    # Drop our metadata plus the mbox-local status headers (read/flagged state is
    # restored via real IMAP flags on APPEND, so these are redundant noise on IMAP).
    for header in (META_FOLDER, META_FLAGS, "Status", "X-Status"):
        del msg[header]   # __delitem__ is a no-op when the header is absent

    try:
        clean = msg.as_bytes()
    except Exception:
        clean = raw
    return (folder.strip() if folder else None), flags, _to_crlf(clean)


def read_mbox_messages(
    path: str, default_folder: str
) -> Iterator[tuple[str, list[str], bytes]]:
    """
    Yield ``(target_folder, flags, clean_bytes)`` for every message in an .mbox.

    Uses :class:`mailbox.mbox` (lazy — never loads the whole file) and
    ``get_bytes`` (clean RFC822, no ``From `` separator). The target folder comes
    from the embedded metadata header, else *default_folder* (mbox is flat).
    """
    box = mailbox.mbox(path)
    try:
        for key in box.iterkeys():
            try:
                raw = box.get_bytes(key)
            except Exception:
                continue
            folder, flags, clean = _strip_meta(raw)
            yield (folder or default_folder), flags, clean
    finally:
        box.close()


def count_mbox_messages(path: str) -> int:
    box = mailbox.mbox(path)
    try:
        return len(box)
    finally:
        box.close()


def read_eml_zip_messages(
    path: str,
    preserve_structure: bool = True,
    single_folder: str = "INBOX",
) -> Iterator[tuple[str, list[str], bytes]]:
    """
    Yield ``(target_folder, flags, clean_bytes)`` for every ``.eml`` in an export ZIP.

    Folder resolution: embedded metadata header first (exact original name); else
    the entry's top-level sub-directory when *preserve_structure*, else *single_folder*.
    """
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith("/") or not name.lower().endswith(".eml"):
                continue
            try:
                raw = zf.read(name)
            except Exception:
                continue
            folder, flags, clean = _strip_meta(raw)
            if not folder:
                if preserve_structure and "/" in name:
                    folder = name.split("/", 1)[0]
                else:
                    folder = single_folder
            yield folder, flags, clean


def count_eml_zip_messages(path: str) -> int:
    with zipfile.ZipFile(path) as zf:
        return sum(
            1 for n in zf.namelist()
            if not n.endswith("/") and n.lower().endswith(".eml")
        )
