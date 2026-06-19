"""
core.py — UI-agnostic export / import orchestration.

Single source of truth for the multi-folder export and import logic, shared by
the Reflex UI (``mailexport.py`` / ``mailimport.py``) and the command-line
interface (``cli.py``). Each operation is a **generator** that yields
:class:`Progress` updates and a final ``Progress(finished=True, …)`` carrying the
summary, so both a synchronous CLI and an async Reflex handler can drive it and
render progress however they like — no logic is duplicated between them.
"""

from __future__ import annotations

import email as email_lib
import mailbox as mailbox_lib
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .imap_utils import (
    META_FLAGS,
    META_FOLDER,
    append_message,
    connect_imap,
    count_eml_zip_messages,
    count_messages_in_folder,
    count_mbox_messages,
    ensure_folder,
    fetch_messages_with_flags,
    get_namespace_info,
    list_folders,
    map_folder_name,
    read_eml_zip_messages,
    read_mbox_messages,
)


@dataclass
class Progress:
    """A single progress tick. The final tick of a run has ``finished=True``."""
    done: int = 0
    total: int = 0
    ok: int = 0
    failed: int = 0
    info: str = ""
    first_error: str = ""
    per_folder: dict = field(default_factory=dict)
    out_path: str = ""
    finished: bool = False


def detect_format(path: str) -> str:
    """Return ``"mbox"`` / ``"eml_zip"`` from a path's extension, else ``""``."""
    p = path.strip().lower()
    if p.endswith(".mbox"):
        return "mbox"
    if p.endswith(".zip"):
        return "eml_zip"
    return ""


# ── Export ──────────────────────────────────────────────────────────────────

def iter_export(
    *,
    host: str,
    port,
    username: str,
    password: str,
    ssl: bool,
    out_dir: str,
    export_fmt: str = "mbox",
    folders: list[str] | None = None,
) -> Iterator[Progress]:
    """
    Export *folders* (default: all) to an mbox or eml/zip file in *out_dir*,
    embedding ``X-Mailexport-*`` metadata for lossless re-import. Yields
    :class:`Progress`; the final tick has ``finished=True`` and ``out_path`` set.
    """
    conn = connect_imap(host, int(port or 993), username, password, ssl)
    try:
        available = list_folders(conn)
        selected = [f for f in available if folders is None or f in folders]
        if not selected:
            raise ValueError("No matching folders to export.")

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        total = sum(count_messages_in_folder(conn, f) for f in selected)
        yield Progress(0, total, info="Counting messages…")

        count = 0
        if export_fmt == "mbox":
            fp = out / f"mailbox_{ts}.mbox"
            mbox = mailbox_lib.mbox(str(fp))
            for folder in selected:
                for raw, flags in fetch_messages_with_flags(conn, folder):
                    msg = email_lib.message_from_bytes(raw)
                    msg[META_FOLDER] = folder
                    msg[META_FLAGS] = " ".join(flags)
                    mm = mailbox_lib.mboxMessage(msg)
                    # \Seen→R, \Flagged→F, \Answered→A (never \Draft/\Deleted→'D':
                    # 'D' means *deleted* in mbox). Full flag set lives in META_FLAGS.
                    if r"\Seen" in flags:
                        mm.add_flag("R")
                    if r"\Flagged" in flags:
                        mm.add_flag("F")
                    if r"\Answered" in flags:
                        mm.add_flag("A")
                    mbox.add(mm)
                    count += 1
                    yield Progress(count, total, info=f"Exporting folder: {folder}")
            mbox.flush()
            mbox.close()
        else:  # eml_zip
            fp = out / f"mailbox_{ts}.zip"
            with zipfile.ZipFile(str(fp), "w", zipfile.ZIP_DEFLATED) as zf:
                for folder in selected:
                    safe = folder.replace("/", "_").replace("\\", "_").strip('"')
                    i = 0
                    for raw, flags in fetch_messages_with_flags(conn, folder):
                        meta = (
                            f"{META_FOLDER}: {folder}\r\n"
                            f"{META_FLAGS}: {' '.join(flags)}\r\n"
                        ).encode("utf-8", "replace")
                        i += 1
                        zf.writestr(f"{safe}/{i:06d}.eml", meta + raw)
                        count += 1
                        yield Progress(count, total, info=f"Exporting folder: {folder}")

        yield Progress(count, total, info=f"Done — {count} messages exported.",
                       out_path=str(fp), finished=True)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ── Import ──────────────────────────────────────────────────────────────────

def iter_import(
    *,
    host: str,
    port,
    username: str,
    password: str,
    ssl: bool,
    src_path: str,
    fmt: str | None = None,
    dest_folder: str = "INBOX",
    preserve_structure: bool = True,
    folder_prefix: str = "",
) -> Iterator[Progress]:
    """
    Import an mbox / eml-zip into the destination account via ``APPEND``,
    recreating folders (namespace-aware) and restoring flags. Yields
    :class:`Progress`; the final tick has ``finished=True`` with ``per_folder``
    and ``first_error`` populated. Raises on fatal (connection / file) errors.
    """
    fmt = fmt or detect_format(src_path)
    if fmt not in ("mbox", "eml_zip"):
        raise ValueError("Unsupported file type — use a .mbox or .zip file.")
    if not Path(src_path).is_file():
        raise FileNotFoundError(f"File not found: {src_path}")

    conn = connect_imap(host, int(port or 993), username, password, ssl)
    try:
        ns_prefix, sep = get_namespace_info(conn)
        prefix = (folder_prefix or "").strip() or ns_prefix
        default_folder = (dest_folder or "INBOX").strip() or "INBOX"
        yield Progress(0, 0, info=f"Destination namespace: prefix '{prefix}' separator '{sep}'.")

        if fmt == "mbox":
            total = count_mbox_messages(src_path)
            source = read_mbox_messages(src_path, default_folder)
        else:
            total = count_eml_zip_messages(src_path)
            source = read_eml_zip_messages(
                src_path, preserve_structure=preserve_structure, single_folder=default_folder,
            )

        ensured: set[str] = set()
        ok_n = fail_n = done = 0
        first_error = ""
        per_folder: dict[str, int] = {}
        for folder, flags, raw in source:
            target = map_folder_name(folder, prefix, sep)
            info = ""
            if target not in ensured:
                ensure_folder(conn, target)
                ensured.add(target)
                info = f"Importing into folder: {target}"
            ok, detail = append_message(conn, target, raw, flags)
            if ok:
                ok_n += 1
                per_folder[target] = per_folder.get(target, 0) + 1
            else:
                fail_n += 1
                if not first_error:
                    first_error = f"{target}: {detail}"
            done += 1
            yield Progress(done, total, ok=ok_n, failed=fail_n, info=info)

        yield Progress(done, total, ok=ok_n, failed=fail_n, first_error=first_error,
                       per_folder=per_folder, finished=True)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
