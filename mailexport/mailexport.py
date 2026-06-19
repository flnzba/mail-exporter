"""
mailexport.py — Reflex app for exporting IMAP mailboxes.

Run with:
    reflex run

Then open http://localhost:3000 in your browser.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import reflex as rx

from .core import iter_export
from .imap_utils import connect_imap, list_folders
from .mailimport import import_page
from .ui_common import PROVIDERS, USERNAME_HINTS, card, feedback_box, field

# Provider presets (PROVIDERS) and per-provider USERNAME_HINTS now live in
# ui_common.py so the import page can reuse them.

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
            i = 0
            for p in iter_export(
                host=self.host.strip(),
                port=self.port,
                username=self.username.strip(),
                password=self.password,
                ssl=self.ssl,
                out_dir=self.out_dir,
                export_fmt=self.export_fmt,
                folders=selected,
            ):
                self.progress = p.done
                self.total = p.total
                if p.info:
                    self.info_msg = p.info
                if p.finished:
                    self.out_path = p.out_path
                    self.export_done = True
                i += 1
                if i % 20 == 0:
                    yield
                    await asyncio.sleep(0)

        except Exception as exc:
            self.err_msg = f"Export failed: {exc}"

        finally:
            self.exporting = False
        yield


# ── Shared UI primitives ──────────────────────────────────────────────────────
# feedback_box / card / field now live in ui_common.py (shared with the import page).

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
                    rx.button("📥 Import", size="1", variant="soft", color_scheme="blue"),
                    href="/import",
                ),
                rx.link(
                    rx.button("? Help", size="1", variant="soft", color_scheme="gray"),
                    href="/help",
                ),
                gap="2", align="center", margin_bottom="1.5rem", padding_top="1rem",
                width="100%", flex_wrap="wrap",
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

                    feedback_box(
                        "Exports embed folder + flag metadata so they re-import losslessly "
                        "on the Import page (📥) — folder structure and read/unread state preserved.",
                        "blue",
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

            # Importing
            help_section(
                "Importing into a mailbox",
                rx.text(
                    "The Import page (📥 top-right, or /import) pushes a previously-exported "
                    ".mbox or .zip back into any IMAP mailbox. You give it a local file path — "
                    "the file is read straight from disk and messages are delivered via IMAP "
                    "APPEND, so there is no browser upload and the ~50 MB webmail / control-panel "
                    "(e.g. Froxlor) import cap never applies.",
                    size="2", margin_bottom="0.75rem",
                ),
                rx.box(
                    help_row("How to start", "Connect to the destination account, enter the path to the .mbox/.zip, set options, then Import."),
                    help_row("Folders & flags", "Files exported by this app embed folder + flag metadata, so folder structure and read/unread state are restored exactly."),
                    help_row("MBOX", "All messages go into one destination folder (an mbox carries no folder structure of its own)."),
                    help_row("EML/ZIP", "One destination folder per archive sub-folder by default; toggle off to import everything into a single folder."),
                    help_row("Folder prefix", "Optional — some servers require all folders under a namespace such as 'INBOX.'."),
                    help_row("⚠ No de-duplication", "IMAP APPEND always adds; running an import twice creates duplicate copies. Import into a fresh folder when unsure."),
                    help_row("Plain / 3rd-party files", "Files without this app's metadata still import: mbox → destination folder, zip → one folder per sub-folder; flags are not restored."),
                    padding="1rem",
                    background="var(--gray-2)",
                    border_radius="0.375rem",
                    border="1px solid var(--gray-4)",
                ),
            ),

            # Command line
            help_section(
                "Command line (no browser)",
                rx.text(
                    "The same export/import runs headlessly — handy for scripting and large migrations:",
                    size="2", margin_bottom="0.5rem",
                ),
                rx.code_block(
                    "# export all folders into ~/mail_export\n"
                    'python -m mailexport export --provider "INWX / webspace.bz" --env --out ~/mail_export\n\n'
                    "# import an mbox/zip into a mailbox (folders + flags restored)\n"
                    "python -m mailexport import --host mail.webspace.bz --env --file ~/mail_export/mailbox.mbox",
                    language="bash",
                ),
                rx.text(
                    "Credentials come from --user/--password or --env (USER_MAIL / USER_PASSWORD). "
                    "Run 'python -m mailexport import --help' for all options.",
                    size="2", color_scheme="gray", margin_top="0.5rem",
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
                    "    ├── imap_utils.py    # IMAP helpers (connect, list, fetch, append)\n"
                    "    ├── core.py          # Shared export/import logic (UI + CLI)\n"
                    "    ├── ui_common.py     # Shared UI primitives + provider presets\n"
                    "    ├── mailimport.py    # Import page: ImportState + UI\n"
                    "    ├── mailexport.py    # Export page: State + UI, app entry point\n"
                    "    ├── cli.py           # Headless CLI (python -m mailexport)\n"
                    "    └── __main__.py      # CLI entry point",
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
app.add_page(import_page, route="/import", title="Mail Importer")
