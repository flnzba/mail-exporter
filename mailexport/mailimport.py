"""
mailimport.py — Reflex state + UI for the *import* side.

Reads a previously-exported ``.mbox`` or ``.zip`` of ``.eml`` files **from a local
file path** and pushes every message into a destination IMAP account via
``IMAP APPEND``. Because we talk IMAP directly there is no ~50 MB webmail/control-
panel upload cap, and because the file is read from disk there is no HTTP upload
at all — multi-GB archives work fine.

When the source was produced by this app's (upgraded) exporter, each message carries
``X-Mailexport-Folder`` / ``X-Mailexport-Flags`` headers, so folders and read/unread
state are reconstructed losslessly. Plain files fall back to sensible defaults.

This module imports only from ``ui_common`` and ``imap_utils`` — never from
``mailexport`` — so registering its page from ``mailexport.py`` creates no cycle.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import reflex as rx

from .imap_utils import (
    append_message,
    connect_imap,
    count_eml_zip_messages,
    count_mbox_messages,
    ensure_folder,
    get_namespace_info,
    list_folders,
    map_folder_name,
    read_eml_zip_messages,
    read_mbox_messages,
)
from .ui_common import PROVIDERS, USERNAME_HINTS, card, feedback_box, field


# ── State ─────────────────────────────────────────────────────────────────────

class ImportState(rx.State):

    # ── Destination connection form ───────────────────────────────────────────
    provider: str = "easyname (Control Panel)"
    host: str = "imap.easyname.com"
    port: str = "993"
    ssl: bool = True
    username: str = ""
    password: str = ""

    # ── Connection runtime ─────────────────────────────────────────────────────
    connecting: bool = False
    connected: bool = False

    # ── Source file ────────────────────────────────────────────────────────────
    src_path: str = ""

    # ── Import settings ────────────────────────────────────────────────────────
    dest_folder: str = "INBOX"          # fallback target (mbox / plain / flattened zip)
    preserve_structure: bool = True     # zip only — one dest folder per archive sub-folder
    folder_prefix: str = ""             # optional namespace prefix, e.g. "INBOX."

    # ── Progress ───────────────────────────────────────────────────────────────
    importing: bool = False
    import_done: bool = False
    progress: int = 0
    total: int = 0
    imported_ok: int = 0
    failed: int = 0

    # ── Feedback ───────────────────────────────────────────────────────────────
    info_msg: str = ""
    err_msg: str = ""

    # ── Computed vars ───────────────────────────────────────────────────────────

    @rx.var
    def progress_pct(self) -> int:
        if self.total == 0:
            return 0
        return min(100, int(self.progress * 100 / self.total))

    @rx.var
    def progress_label(self) -> str:
        if self.total == 0:
            return "Counting messages…"
        return f"{self.progress} / {self.total} messages  ({self.progress_pct}%)"

    @rx.var
    def detected_fmt(self) -> str:
        p = self.src_path.strip().lower()
        if p.endswith(".mbox"):
            return "mbox"
        if p.endswith(".zip"):
            return "eml_zip"
        return ""

    @rx.var
    def fmt_known(self) -> bool:
        return self.detected_fmt in ("mbox", "eml_zip")

    @rx.var
    def fmt_label(self) -> str:
        return {
            "mbox": "Detected: MBOX file. Folders restore from embedded metadata; "
                    "files without it import into the destination folder below.",
            "eml_zip": "Detected: EML/ZIP archive.",
        }.get(self.detected_fmt, "Enter a path ending in .mbox or .zip")

    @rx.var
    def is_zip(self) -> bool:
        return self.detected_fmt == "eml_zip"

    @rx.var
    def username_hint(self) -> str:
        return USERNAME_HINTS.get(self.provider, "")

    @rx.var
    def show_username_hint(self) -> bool:
        return self.provider in USERNAME_HINTS

    @rx.var
    def result_label(self) -> str:
        msg = f"{self.imported_ok} message(s) imported"
        if self.failed:
            msg += f", {self.failed} failed"
        return msg + "."

    # ── Provider preset ─────────────────────────────────────────────────────────

    @rx.event
    def set_provider(self, provider: str):
        self.provider = provider
        if provider in PROVIDERS:
            h, p, s = PROVIDERS[provider]
            self.host = h
            self.port = p
            self.ssl = s

    # ── Form field setters (Reflex 0.9 needs explicit handlers) ──────────────────

    @rx.event
    def set_host(self, host: str):
        self.host = host

    @rx.event
    def set_port(self, port: str):
        self.port = port

    @rx.event
    def set_username(self, username: str):
        self.username = username

    @rx.event
    def set_password(self, password: str):
        self.password = password

    @rx.event
    def set_ssl(self, ssl: bool):
        self.ssl = ssl

    @rx.event
    def set_src_path(self, src_path: str):
        self.src_path = src_path

    @rx.event
    def set_dest_folder(self, dest_folder: str):
        self.dest_folder = dest_folder

    @rx.event
    def set_preserve_structure(self, preserve_structure: bool):
        self.preserve_structure = preserve_structure

    @rx.event
    def set_folder_prefix(self, folder_prefix: str):
        self.folder_prefix = folder_prefix

    # ── Connect to destination ────────────────────────────────────────────────

    @rx.event
    async def connect(self):
        if not self.host.strip() or not self.username.strip() or not self.password:
            self.err_msg = "Host, username and password are all required."
            return

        self.connecting = True
        self.connected = False
        self.err_msg = ""
        self.info_msg = "Connecting to destination IMAP server…"
        yield

        try:
            await asyncio.sleep(0.05)
            conn = connect_imap(
                self.host.strip(),
                int(self.port or "993"),
                self.username.strip(),
                self.password,
                self.ssl,
            )
            folders = list_folders(conn)
            conn.logout()

            self.connected = True
            self.import_done = False
            self.info_msg = f"Connected — destination has {len(folders)} folders."

        except Exception as exc:
            self.err_msg = str(exc)
            self.info_msg = ""

        finally:
            self.connecting = False
        yield

    # ── Import ──────────────────────────────────────────────────────────────────

    @rx.event
    async def start_import(self):
        path = self.src_path.strip()
        fmt = self.detected_fmt

        if not path:
            self.err_msg = "Enter the path to a .mbox or .zip file."
            return
        if not Path(path).is_file():
            self.err_msg = f"File not found: {path}"
            return
        if fmt not in ("mbox", "eml_zip"):
            self.err_msg = "Unsupported file type — use a .mbox or .zip file."
            return
        # mbox / flattened-zip imports need an explicit destination folder.
        if not self.dest_folder.strip() and not (fmt == "eml_zip" and self.preserve_structure):
            self.err_msg = "Enter a destination folder."
            return

        self.importing = True
        self.import_done = False
        self.progress = 0
        self.total = 0
        self.imported_ok = 0
        self.failed = 0
        self.err_msg = ""
        self.info_msg = "Preparing import…"
        yield

        try:
            conn = connect_imap(
                self.host.strip(),
                int(self.port or "993"),
                self.username.strip(),
                self.password,
                self.ssl,
            )

            self.info_msg = "Counting messages in source file…"
            yield

            # Detect the destination namespace so folders land where the server
            # expects them (e.g. Froxlor/Dovecot nests everything under "INBOX.").
            ns_prefix, sep = get_namespace_info(conn)
            prefix = self.folder_prefix.strip() or ns_prefix
            default_folder = self.dest_folder.strip() or "INBOX"
            if prefix or sep != "/":
                self.info_msg = f"Destination namespace: prefix '{prefix}' separator '{sep}'."
                yield

            if fmt == "mbox":
                self.total = count_mbox_messages(path)
                msg_iter = read_mbox_messages(path, default_folder)
            else:
                self.total = count_eml_zip_messages(path)
                msg_iter = read_eml_zip_messages(
                    path,
                    preserve_structure=self.preserve_structure,
                    single_folder=default_folder,
                )
            yield

            ensured: set[str] = set()
            first_error = ""
            count = 0
            for folder, flags, raw in msg_iter:
                target = map_folder_name(folder, prefix, sep)
                if target not in ensured:
                    ensure_folder(conn, target)
                    ensured.add(target)
                    self.info_msg = f"Importing into folder: {target}"
                    yield
                ok, detail = append_message(conn, target, raw, flags)
                if ok:
                    self.imported_ok += 1
                else:
                    self.failed += 1
                    if not first_error:
                        first_error = f"{target}: {detail}"
                count += 1
                self.progress = count
                if count % 20 == 0:
                    yield
                    await asyncio.sleep(0)

            conn.logout()
            done = f"{self.imported_ok} message(s) imported"
            if self.failed:
                done += f", {self.failed} failed"

            if self.imported_ok == 0 and self.failed > 0:
                # Total failure — surface the reason, no success box.
                self.info_msg = ""
                self.err_msg = (
                    f"No messages were imported. First error — {first_error}. "
                    "Tip: for Froxlor / Dovecot try setting the folder prefix to "
                    "'INBOX.', or import into a single existing folder."
                )
            else:
                self.import_done = True
                self.info_msg = f"Done — {done}."
                if self.failed:
                    self.err_msg = f"{self.failed} message(s) failed. First error — {first_error}."

        except Exception as exc:
            self.err_msg = f"Import failed: {exc}"

        finally:
            self.importing = False
        yield


# ── Connection card (shared shape with the export page) ────────────────────────

def _connection_card() -> rx.Component:
    return card(
        "① Destination IMAP Connection",

        field(
            "Provider",
            rx.select(
                list(PROVIDERS.keys()),
                value=ImportState.provider,
                on_change=ImportState.set_provider,
                width="100%",
            ),
        ),

        rx.grid(
            field(
                "IMAP host",
                rx.input(
                    value=ImportState.host,
                    on_change=ImportState.set_host,
                    placeholder="imap.example.com",
                    width="100%",
                ),
            ),
            field(
                "Port",
                rx.input(
                    value=ImportState.port,
                    on_change=ImportState.set_port,
                    type="number",
                    width="100%",
                ),
            ),
            columns=rx.breakpoints(initial="1", sm="2"),
            gap="3", width="100%",
        ),

        field(
            "Username / Mailbox name",
            rx.input(
                value=ImportState.username,
                on_change=ImportState.set_username,
                placeholder="Email address or mailbox name",
                width="100%",
            ),
        ),

        rx.cond(
            ImportState.show_username_hint,
            feedback_box(ImportState.username_hint, "orange"),
        ),

        rx.box(height="0.5rem"),

        field(
            "Password",
            rx.input(
                value=ImportState.password,
                on_change=ImportState.set_password,
                placeholder="Password or app password",
                type="password",
                width="100%",
            ),
        ),

        rx.flex(
            rx.switch(checked=ImportState.ssl, on_change=ImportState.set_ssl),
            rx.text("Use SSL / TLS (recommended)", size="2"),
            gap="2", align="center", margin_bottom="0.75rem",
        ),

        rx.button(
            "Connect",
            on_click=ImportState.connect,
            loading=ImportState.connecting,
            disabled=ImportState.connecting,
            color_scheme="blue",
            width="100%", size="3",
        ),

        rx.cond(
            ImportState.info_msg != "",
            feedback_box(
                ImportState.info_msg,
                rx.cond(ImportState.connected, "green", "blue"),
            ),
        ),
        rx.cond(
            ImportState.err_msg != "",
            feedback_box(ImportState.err_msg, "red"),
        ),
    )


# ── Import page ─────────────────────────────────────────────────────────────────

def import_page() -> rx.Component:
    return rx.container(
        rx.vstack(

            # Header
            rx.flex(
                rx.text("📥", font_size="2.2rem", line_height="1"),
                rx.vstack(
                    rx.heading("Mail Importer", size=rx.breakpoints(initial="6", sm="7")),
                    rx.text(
                        "Push a previously-exported .mbox or .zip into any IMAP mailbox — "
                        "no upload size limit.",
                        size="2", color_scheme="gray",
                    ),
                    gap="0", align_items="flex-start",
                ),
                rx.spacer(),
                rx.link(
                    rx.button("← Export", size="1", variant="soft", color_scheme="gray"),
                    href="/",
                ),
                rx.link(
                    rx.button("? Help", size="1", variant="soft", color_scheme="gray"),
                    href="/help",
                ),
                gap="2", align="center", margin_bottom="1.5rem", padding_top="1rem",
                width="100%", flex_wrap="wrap",
            ),

            # ── Step 1 · Connection ───────────────────────────────────────
            _connection_card(),

            # ── Step 2 · Source file ──────────────────────────────────────
            rx.cond(
                ImportState.connected,
                card(
                    "② Source File",
                    field(
                        "Path to .mbox or .zip file",
                        rx.input(
                            value=ImportState.src_path,
                            on_change=ImportState.set_src_path,
                            placeholder=str(Path.home() / "mail_export" / "mailbox_20250101_120000.mbox"),
                            width="100%",
                        ),
                    ),
                    feedback_box(
                        ImportState.fmt_label,
                        rx.cond(ImportState.fmt_known, "blue", "orange"),
                    ),
                    feedback_box(
                        "The file is read directly from disk on this machine — there is no "
                        "upload and no size limit. This is how you bypass the ~50 MB "
                        "webmail / control-panel (e.g. Froxlor) import cap.",
                        "blue",
                    ),
                ),
            ),

            # ── Step 3 · Destination options ──────────────────────────────
            rx.cond(
                ImportState.connected,
                card(
                    "③ Destination Options",
                    field(
                        "Destination folder",
                        rx.input(
                            value=ImportState.dest_folder,
                            on_change=ImportState.set_dest_folder,
                            placeholder="INBOX",
                            width="100%",
                        ),
                        note="Fallback folder for files without folder metadata, or when structure isn't preserved. The server's namespace prefix is applied automatically.",
                    ),

                    rx.cond(
                        ImportState.is_zip,
                        rx.box(
                            rx.flex(
                                rx.switch(
                                    checked=ImportState.preserve_structure,
                                    on_change=ImportState.set_preserve_structure,
                                ),
                                rx.text("Recreate one folder per archive sub-folder", size="2"),
                                gap="2", align="center", margin_bottom="0.4rem",
                            ),
                            feedback_box(
                                "Sub-folder names are imported literally; hierarchy separators "
                                "were flattened to '_' during export and cannot be reliably restored. "
                                "Turn this off to import everything into the destination folder above.",
                                "blue",
                            ),
                            width="100%",
                        ),
                    ),

                    rx.box(height="0.5rem"),

                    field(
                        "Folder name prefix (optional)",
                        rx.input(
                            value=ImportState.folder_prefix,
                            on_change=ImportState.set_folder_prefix,
                            placeholder="e.g. INBOX.",
                            width="100%",
                        ),
                        note="Some servers require all folders under a namespace such as 'INBOX.'.",
                    ),
                ),
            ),

            # ── Step 4 · Import ───────────────────────────────────────────
            rx.cond(
                ImportState.connected,
                card(
                    "④ Import",
                    feedback_box(
                        "⚠ APPEND does not de-duplicate — running an import twice creates "
                        "duplicate copies. Import into a fresh / empty folder when in doubt.",
                        "orange",
                    ),

                    rx.box(height="0.75rem"),

                    rx.button(
                        "Import Messages",
                        on_click=ImportState.start_import,
                        loading=ImportState.importing,
                        disabled=ImportState.importing,
                        color_scheme="green", size="3", width="100%",
                    ),

                    rx.cond(
                        ImportState.importing,
                        rx.vstack(
                            rx.box(height="0.5rem"),
                            rx.text(ImportState.info_msg, size="2", color_scheme="gray"),
                            rx.progress(value=ImportState.progress_pct, width="100%"),
                            rx.text(ImportState.progress_label, size="1", color_scheme="gray"),
                            width="100%", gap="2",
                        ),
                    ),

                    rx.cond(
                        ImportState.import_done,
                        rx.box(
                            rx.text("✅ Import complete!", size="3", weight="bold", color="#166534"),
                            rx.text(ImportState.result_label, size="2", color="#166534"),
                            background="#f0fdf4", border="1px solid #86efac",
                            border_radius="0.375rem", padding="0.875rem",
                            width="100%", margin_top="0.75rem",
                        ),
                    ),
                ),
            ),

            rx.text(
                "All processing happens locally. No data leaves your machine.",
                size="1", color_scheme="gray", text_align="center", padding_bottom="1.5rem",
            ),

            gap="0", width="100%",
        ),
        max_width="720px", padding_x="1rem",
    )
