"""
ui_common.py — Shared, stateless UI primitives and provider presets.

Imported by both the export page (``mailexport.py``) and the import page
(``mailimport.py``). Keeping these here lets both pages reuse them without a
circular import (neither page module imports the other). Imports only ``reflex``.
"""

from __future__ import annotations

import reflex as rx


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
    "INWX / webspace.bz":       ("mail.webspace.bz",           "993", True),
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
    "INWX / webspace.bz": (
        "Use your full email address. Sub-folders are created under the server's "
        "'INBOX.' namespace automatically (Froxlor / Dovecot)."
    ),
}


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
