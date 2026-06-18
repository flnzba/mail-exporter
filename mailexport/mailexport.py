"""
mailexport.py — Reflex app for exporting IMAP mailboxes.

Run with:
    reflex run

Then open http://localhost:3000 in your browser.
"""

from __future__ import annotations

import asyncio
import email as email_lib
import mailbox as mailbox_lib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import reflex as rx

from .imap_utils import (
    connect_imap,
    count_messages_in_folder,
    fetch_raw_messages,
    list_folders,
)

# ── Provider presets ─────────────────────────────────────────────────────────
# (host, port-string, ssl)
PROVIDERS: dict[str, tuple[str, str, bool]] = {
    "easyname (Control Panel)": ("imap.easyname.com",         "993", True),
    "easyname (CloudPit)":      ("imap.easyname.com",         "993", True),
    "Gmail":                    ("imap.gmail.com",             "993", True),
    "Outlook/Office365":        ("outlook.office365.com",      "993", True),
    "Yahoo Mail":               ("imap.mail.yahoo.com",        "993", True),
    "GMX":                      ("imap.gmx.com",               "993", True),
    "iCloud":                   ("imap.mail.me.com",           "993", True),
    "Fastmail":                 ("imap.fastmail.com",          "993", True),
    "Custom":                   ("",                           "993", True),
}

# Per-provider username label / hint shown below the username field
USERNAME_HINTS: dict[str, str] = {
    "easyname (Control Panel)": (
        "⚠ Use the mailbox name from your Control Panel → Datasheet "
        "(e.g. abc123) — NOT the email address."
    ),
    "easyname (CloudPit)": (
        "Use your full email address (e.g. you@yourdomain.at)."
    ),
    "Gmail": (
        "Use an App Password if you have 2-Step Verification enabled."
    ),
    "Yahoo Mail": (
        "Use an App Password (generated at login.yahoo.com/account/security)."
    ),
    "iCloud": (
        "Use an App-Specific Password from appleid.apple.com."
    ),
}

DEFAULT_OUT = str(Path.home() / "mail_export")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class FolderItem:
    """A single IMAP folder with its selection state."""
    name: str
    checked: bool = True


# ── State ─────────────────────────────────────────────────────────────────────

class State(rx.State):

    # ── Connection form ───────────────────────────────────────────────────────
    provider: str = "easyname (Control Panel)"
    host: str = "imap.easyname.com"
    port: str = "993"
    ssl: bool = True
    username: str = ""
    password: str = ""

    # ── Runtime ───────────────────────────────────────────────────────────────
    connecting: bool = False
    connected: bool = False

    # ── Folder list ───────────────────────────────────────────────────────────
    folders: list[FolderItem] = []

    # ── Export settings ───────────────────────────────────────────────────────
    export_fmt: str = "mbox"
    out_dir: str = DEFAULT_OUT

    # ── Progress ──────────────────────────────────────────────────────────────
    exporting: bool = False
    export_done: bool = False
    progress: int = 0
    total: int = 0
    out_path: str = ""

    # ── Feedback ──────────────────────────────────────────────────────────────
    info_msg: str = ""
    err_msg: str = ""

    # ── Computed vars ─────────────────────────────────────────────────────────

    @rx.var
    def progress_pct(self) -> int:
        if self.total == 0:
            return 0
        return min(100, int(self.progress * 100 / self.total))

    @rx.var
    def selected_count(self) -> int:
        return sum(1 for f in self.folders if f.checked)

    @rx.var
    def folder_count(self) -> int:
        return len(self.folders)

    @rx.var
    def selection_label(self) -> str:
        return f"{self.selected_count} / {len(self.folders)} selected"

    @rx.var
    def progress_label(self) -> str:
        if self.total == 0:
            return "Counting messages…"
        return f"{self.progress} / {self.total} messages  ({self.progress_pct}%)"

    @rx.var
    def username_hint(self) -> str:
        return USERNAME_HINTS.get(self.provider, "")

    @rx.var
    def show_username_hint(self) -> bool:
        return self.provider in USERNAME_HINTS

    # ── Provider preset ───────────────────────────────────────────────────────

    @rx.event
    def set_provider(self, provider: str):
        self.provider = provider
        if provider in PROVIDERS:
            h, p, s = PROVIDERS[provider]
            self.host = h
            self.port = p
            self.ssl = s

    # ── Form field setters ────────────────────────────────────────────────────
    # Reflex 0.9 no longer auto-generates `set_<var>` handlers, so define them.

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
    def set_export_fmt(self, export_fmt: str):
        self.export_fmt = export_fmt

    @rx.event
    def set_out_dir(self, out_dir: str):
        self.out_dir = out_dir

    # ── Connect ───────────────────────────────────────────────────────────────

    @rx.event
    async def connect(self):
        if not self.host.strip() or not self.username.strip() or not self.password:
            self.err_msg = "Host, username and password are all required."
            return

        self.connecting = True
        self.connected = False
        self.err_msg = ""
        self.info_msg = "Connecting to IMAP server…"
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
            raw_folders = list_folders(conn)
            conn.logout()

            self.folders = [FolderItem(name=f, checked=True) for f in raw_folders]
            self.connected = True
            self.export_done = False
            self.info_msg = f"Connected — {len(raw_folders)} folders found."

        except Exception as exc:
            self.err_msg = str(exc)
            self.info_msg = ""

        finally:
            self.connecting = False
        yield

    # ── Folder selection ──────────────────────────────────────────────────────

    @rx.event
    def set_folder_checked(self, index: int, checked: bool):
        self.folders[index].checked = checked

    @rx.event
    def select_all(self):
        for i in range(len(self.folders)):
            self.folders[i].checked = True

    @rx.event
    def deselect_all(self):
        for i in range(len(self.folders)):
            self.folders[i].checked = False

    # ── Export ────────────────────────────────────────────────────────────────

    @rx.event
    async def start_export(self):
        selected = [f.name for f in self.folders if f.checked]
        if not selected:
            self.err_msg = "Select at least one folder before exporting."
            return

        self.exporting = True
        self.export_done = False
        self.progress = 0
        self.total = 0
        self.err_msg = ""
        self.out_path = ""
        self.info_msg = "Preparing export…"
        yield

        try:
            out_path = Path(self.out_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            conn = connect_imap(
                self.host.strip(),
                int(self.port or "993"),
                self.username.strip(),
                self.password,
                self.ssl,
            )

            self.info_msg = "Counting messages across selected folders…"
            yield
            total = 0
            for folder in selected:
                total += count_messages_in_folder(conn, folder)
            self.total = total
            yield

            count = 0

            if self.export_fmt == "mbox":
                file_path = out_path / f"mailbox_{ts}.mbox"
                mbox = mailbox_lib.mbox(str(file_path))

                for folder in selected:
                    self.info_msg = f"Exporting folder: {folder}"
                    yield
                    for raw in fetch_raw_messages(conn, folder):
                        msg = email_lib.message_from_bytes(raw)
                        mbox.add(msg)
                        count += 1
                        self.progress = count
                        if count % 20 == 0:
                            yield
                            await asyncio.sleep(0)

                mbox.flush()
                mbox.close()

            else:  # eml_zip
                file_path = out_path / f"mailbox_{ts}.zip"
                with zipfile.ZipFile(str(file_path), "w", zipfile.ZIP_DEFLATED) as zf:
                    for folder in selected:
                        self.info_msg = f"Exporting folder: {folder}"
                        yield
                        safe_name = folder.replace("/", "_").replace("\\", "_").strip('"')
                        for i, raw in enumerate(fetch_raw_messages(conn, folder)):
                            zf.writestr(f"{safe_name}/{i + 1:06d}.eml", raw)
                            count += 1
                            self.progress = count
                            if count % 20 == 0:
                                yield
                                await asyncio.sleep(0)

            conn.logout()
            self.out_path = str(file_path)
            self.export_done = True
            self.info_msg = f"Done — {count} messages exported."

        except Exception as exc:
            self.err_msg = f"Export failed: {exc}"

        finally:
            self.exporting = False
        yield


# ── Shared UI primitives ──────────────────────────────────────────────────────

def feedback_box(text, color) -> rx.Component:
    # `color` may be a literal ("red") or a reflex Var (rx.cond(...)), so resolve
    # the palette with rx.match instead of indexing a plain dict at compile time.
    bg = rx.match(
        color,
        ("green", "#f0fdf4"),
        ("red", "#fef2f2"),
        ("orange", "#fff7ed"),
        "#eff6ff",  # blue / default
    )
    fg = rx.match(
        color,
        ("green", "#166534"),
        ("red", "#991b1b"),
        ("orange", "#9a3412"),
        "#1e40af",
    )
    border_color = rx.match(
        color,
        ("green", "#86efac"),
        ("red", "#fca5a5"),
        ("orange", "#fdba74"),
        "#93c5fd",
    )
    return rx.box(
        rx.text(text, size="2", color=fg),
        background=bg,
        border_width="1px",
        border_style="solid",
        border_color=border_color,
        border_radius="0.375rem",
        padding="0.5rem 0.875rem",
        width="100%",
        margin_top="0.5rem",
    )


def card(title: str, *children) -> rx.Component:
    return rx.box(
        rx.heading(title, size="4", margin_bottom="0.6rem", color_scheme="blue"),
        rx.divider(margin_bottom="0.75rem"),
        *children,
        padding="1.25rem",
        border_radius="0.5rem",
        border="1px solid var(--gray-5)",
        background="var(--gray-1)",
        width="100%",
        margin_bottom="1rem",
        box_shadow="0 1px 3px rgba(0,0,0,.08)",
    )


def field(label: str, control: rx.Component, note: str = "") -> rx.Component:
    children = [
        rx.text(label, size="1", weight="medium", color_scheme="gray", margin_bottom="0.2rem"),
        control,
    ]
    if note:
        children.append(rx.text(note, size="1", color_scheme="gray", margin_top="0.2rem"))
    return rx.box(*children, margin_bottom="0.75rem", width="100%")


def folder_row(item: FolderItem, idx: int) -> rx.Component:
    return rx.flex(
        rx.checkbox(
            checked=item.checked,
            on_change=State.set_folder_checked(idx),
        ),
        rx.text(item.name, size="2"),
        align="center",
        gap="2",
        padding_y="2px",
        width="100%",
    )


# ── Main page ─────────────────────────────────────────────────────────────────

def index() -> rx.Component:
    return rx.container(
        rx.vstack(

            # Header
            rx.flex(
                rx.text("📬", font_size="2.2rem", line_height="1"),
                rx.vstack(
                    rx.heading("Mail Exporter", size=rx.breakpoints(initial="6", sm="7")),
                    rx.text(
                        "Export any IMAP mailbox to MBOX or EML/ZIP — no mail client needed.",
                        size="2", color_scheme="gray",
                    ),
                    gap="0", align_items="flex-start",
                ),
                rx.spacer(),
                rx.link(
                    rx.button("? Help", size="1", variant="soft", color_scheme="gray"),
                    href="/help",
                ),
                gap="3", align="center", margin_bottom="1.5rem", padding_top="1rem",
                width="100%",
            ),

            # ── Step 1 · Connection ───────────────────────────────────────
            card(
                "① IMAP Connection",

                field(
                    "Provider",
                    rx.select(
                        list(PROVIDERS.keys()),
                        value=State.provider,
                        on_change=State.set_provider,
                        width="100%",
                    ),
                ),

                rx.grid(
                    field(
                        "IMAP host",
                        rx.input(
                            value=State.host,
                            on_change=State.set_host,
                            placeholder="imap.example.com",
                            width="100%",
                        ),
                    ),
                    field(
                        "Port",
                        rx.input(
                            value=State.port,
                            on_change=State.set_port,
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
                        value=State.username,
                        on_change=State.set_username,
                        placeholder="Email address or mailbox name",
                        width="100%",
                    ),
                ),

                # Provider-specific username hint
                rx.cond(
                    State.show_username_hint,
                    feedback_box(State.username_hint, "orange"),
                ),

                rx.box(height="0.5rem"),

                field(
                    "Password",
                    rx.input(
                        value=State.password,
                        on_change=State.set_password,
                        placeholder="Password or app password",
                        type="password",
                        width="100%",
                    ),
                ),

                rx.flex(
                    rx.switch(checked=State.ssl, on_change=State.set_ssl),
                    rx.text("Use SSL / TLS (recommended)", size="2"),
                    gap="2", align="center", margin_bottom="0.75rem",
                ),

                rx.button(
                    "Connect",
                    on_click=State.connect,
                    loading=State.connecting,
                    disabled=State.connecting,
                    color_scheme="blue",
                    width="100%", size="3",
                ),

                rx.cond(
                    State.info_msg != "",
                    feedback_box(
                        State.info_msg,
                        rx.cond(State.connected, "green", "blue"),
                    ),
                ),
                rx.cond(
                    State.err_msg != "",
                    feedback_box(State.err_msg, "red"),
                ),
            ),

            # ── Step 2 · Folder selection ─────────────────────────────────
            rx.cond(
                State.connected,
                card(
                    "② Select Folders",
                    rx.flex(
                        rx.button("✓ All", on_click=State.select_all, size="1", variant="soft", color_scheme="blue"),
                        rx.button("✗ None", on_click=State.deselect_all, size="1", variant="soft", color_scheme="gray"),
                        rx.spacer(),
                        rx.badge(State.selection_label, color_scheme="blue", variant="soft"),
                        gap="2", align="center", width="100%", margin_bottom="0.5rem",
                    ),
                    rx.box(
                        rx.foreach(State.folders, folder_row),
                        max_height="260px", overflow_y="auto",
                        padding="0.5rem",
                        border="1px solid var(--gray-4)",
                        border_radius="0.375rem",
                        background="var(--gray-2)",
                    ),
                ),
            ),

            # ── Step 3 · Export ───────────────────────────────────────────
            rx.cond(
                State.connected,
                card(
                    "③ Export",
                    rx.grid(
                        field(
                            "Format",
                            rx.select(
                                ["mbox", "eml_zip"],
                                value=State.export_fmt,
                                on_change=State.set_export_fmt,
                                width="100%",
                            ),
                        ),
                        field(
                            "Output directory",
                            rx.input(
                                value=State.out_dir,
                                on_change=State.set_out_dir,
                                width="100%",
                            ),
                        ),
                        columns=rx.breakpoints(initial="1", sm="2"),
                        gap="3", width="100%",
                    ),

                    rx.cond(
                        State.export_fmt == "mbox",
                        feedback_box(
                            "MBOX — single file, importable into Thunderbird, Apple Mail, mutt.",
                            "blue",
                        ),
                        feedback_box(
                            "EML/ZIP — one .eml file per message inside a ZIP archive.",
                            "blue",
                        ),
                    ),

                    rx.box(height="0.75rem"),

                    rx.button(
                        "Export Mailbox",
                        on_click=State.start_export,
                        loading=State.exporting,
                        disabled=State.exporting,
                        color_scheme="green", size="3", width="100%",
                    ),

                    rx.cond(
                        State.exporting,
                        rx.vstack(
                            rx.box(height="0.5rem"),
                            rx.text(State.info_msg, size="2", color_scheme="gray"),
                            rx.progress(value=State.progress_pct, width="100%"),
                            rx.text(State.progress_label, size="1", color_scheme="gray"),
                            width="100%", gap="2",
                        ),
                    ),

                    rx.cond(
                        State.export_done,
                        rx.box(
                            rx.text("✅ Export complete!", size="3", weight="bold", color="#166534"),
                            rx.text(State.out_path, size="2", color="#166534", font_family="monospace"),
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


# ── Help / Manual page ────────────────────────────────────────────────────────

def help_section(title: str, *children) -> rx.Component:
    return rx.box(
        rx.heading(title, size="4", margin_bottom="0.5rem"),
        *children,
        margin_bottom="1.5rem",
    )


def help_row(label: str, value: str) -> rx.Component:
    # Stack label above value on phones; align side-by-side from the sm breakpoint.
    return rx.flex(
        rx.text(label, size="2", weight="medium",
                min_width=rx.breakpoints(initial="auto", sm="180px")),
        rx.text(value, size="2", color_scheme="gray"),
        direction=rx.breakpoints(initial="column", sm="row"),
        gap=rx.breakpoints(initial="1", sm="3"),
        padding_y="3px", width="100%",
    )


def help_page() -> rx.Component:
    return rx.container(
        rx.vstack(

            rx.flex(
                rx.link(
                    rx.button("← Back", size="1", variant="soft", color_scheme="gray"),
                    href="/",
                ),
                rx.heading("Mail Exporter — Manual",
                           size=rx.breakpoints(initial="5", sm="6"), margin_left="1rem"),
                align="center", flex_wrap="wrap", gap="2",
                margin_bottom="1.5rem", padding_top="1rem", width="100%",
            ),

            # Quick-start
            help_section(
                "Quick-start",
                rx.box(
                    rx.text("1.  Create a virtual environment (optional but recommended):", size="2", weight="medium"),
                    rx.code_block("python -m venv .venv && source .venv/bin/activate", language="bash"),
                    rx.text("2.  Install dependencies:", size="2", weight="medium", margin_top="0.75rem"),
                    rx.code_block("pip install -r requirements.txt", language="bash"),
                    rx.text("3.  Initialise Reflex (first run only — downloads the frontend runtime):", size="2", weight="medium", margin_top="0.75rem"),
                    rx.code_block("reflex init", language="bash"),
                    rx.text("4.  Run the app:", size="2", weight="medium", margin_top="0.75rem"),
                    rx.code_block("reflex run", language="bash"),
                    rx.text("Then open http://localhost:3000 in your browser.", size="2", margin_top="0.5rem"),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                ),
            ),

            # easyname
            help_section(
                "easyname",
                rx.text(
                    "easyname runs two distinct hosting platforms. "
                    "Choose the correct preset and username format:",
                    size="2", margin_bottom="0.75rem",
                ),
                rx.box(
                    rx.heading("Control Panel (legacy accounts)", size="3", margin_bottom="0.4rem"),
                    help_row("IMAP server", "imap.easyname.com"),
                    help_row("Port / Security", "993 / SSL"),
                    help_row("Username", "Mailbox name from Control Panel → Datasheet (e.g. abc12345)"),
                    help_row("Password", "The mailbox password set in the Control Panel"),
                    rx.text(
                        "The username is NOT the email address — it is a system-generated short name "
                        "visible in your Control Panel under Webhosting → Datasheet.",
                        size="2", color_scheme="orange", margin_top="0.5rem",
                    ),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                    margin_bottom="0.75rem",
                ),
                rx.box(
                    rx.heading("CloudPit (Webhosting 2.0)", size="3", margin_bottom="0.4rem"),
                    help_row("IMAP server", "imap.easyname.com"),
                    help_row("Port / Security", "993 / SSL"),
                    help_row("Username", "Full email address (e.g. you@yourdomain.at)"),
                    help_row("Password", "The mailbox password set in CloudPit"),
                    rx.text(
                        "Select 'Password, normal' authentication — CRAM-MD5 is not supported.",
                        size="2", color_scheme="blue", margin_top="0.5rem",
                    ),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                ),
            ),

            # Other providers
            help_section(
                "Other providers",
                rx.box(
                    help_row("Gmail",        "App Password required when 2-Step Verification is on → myaccount.google.com/apppasswords"),
                    help_row("Yahoo Mail",   "App Password required → login.yahoo.com/account/security"),
                    help_row("iCloud",       "App-Specific Password required → appleid.apple.com"),
                    help_row("Outlook/M365", "Basic-auth IMAP must be enabled by your admin in the M365 Admin Center"),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                ),
            ),

            # Export formats
            help_section(
                "Export formats",
                rx.box(
                    rx.heading("MBOX (.mbox)", size="3", margin_bottom="0.3rem"),
                    rx.text(
                        "A single file containing all messages in sequence. "
                        "Importable in Thunderbird (Tools → Import), Apple Mail, mutt, and most Unix mail tools.",
                        size="2",
                    ),
                    rx.heading("EML/ZIP (.zip)", size="3", margin_top="0.75rem", margin_bottom="0.3rem"),
                    rx.text(
                        "One .eml file per message, organised in sub-folders matching the IMAP folder tree, "
                        "all packed into a ZIP archive. Each .eml file can be opened individually by any mail client.",
                        size="2",
                    ),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                ),
            ),

            # Troubleshooting
            help_section(
                "Troubleshooting",
                rx.box(
                    help_row("[AUTHENTICATIONFAILED]",  "Wrong username or password. For easyname Control Panel check the Datasheet for the mailbox name."),
                    help_row("Connection refused",       "Check host name and port. Toggle SSL off if using port 143."),
                    help_row("Progress bar freezes",     "Normal on slow connections — the bar updates every 20 messages."),
                    help_row("Outlook / M365 login fail", "Your tenant admin may need to re-enable IMAP basic auth in the M365 Admin Center."),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                ),
            ),

            # Project layout
            help_section(
                "Project layout",
                rx.code_block(
                    "mailexport/\n"
                    "├── rxconfig.py          # Reflex config (ports, app name)\n"
                    "├── requirements.txt     # Only 'reflex' — everything else is stdlib\n"
                    "├── README.md            # This manual as plain Markdown\n"
                    "└── mailexport/\n"
                    "    ├── __init__.py\n"
                    "    ├── imap_utils.py    # IMAP helpers (connect, list, fetch)\n"
                    "    └── mailexport.py    # Reflex state + UI (single file)",
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


# ── App ───────────────────────────────────────────────────────────────────────

# Theme is configured via RadixThemesPlugin in rxconfig.py (App(theme=...) is
# deprecated in Reflex 0.9.x).
app = rx.App()
app.add_page(index, route="/",     title="Mail Exporter")
app.add_page(help_page, route="/help", title="Mail Exporter — Manual")
