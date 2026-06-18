# 📬 Mail Exporter

A self-hosted, browser-based tool to export any IMAP mailbox to **MBOX** or
**EML/ZIP** format — no mail client installation required.  
Built with pure Python + [Reflex](https://reflex.dev).  
The in-app manual is available at **http://localhost:3000/help** once running.

---

## Quick-start

### Prerequisites

| Requirement | Version |
|-------------|---------|
| Python      | 3.10 +  |
| Node.js     | 18 +    *(Reflex compiles its React frontend once on first run)* |

> Use a virtual environment: `python -m venv .venv && source .venv/bin/activate`

### Install & run

```bash
cd mailexport
pip install -r requirements.txt
reflex init        # downloads frontend runtime — once only (~30 s)
reflex run         # → http://localhost:3000
```

---

## easyname

easyname runs **two distinct hosting platforms**. Choose the correct preset:

### Control Panel (legacy / original accounts)

| Setting    | Value |
|------------|-------|
| IMAP host  | `imap.easyname.com` |
| Port       | 993 (SSL) |
| Username   | **Mailbox name** from Control Panel → Webhosting → Datasheet (e.g. `abc12345`) — **not** the email address |
| Password   | Mailbox password from Control Panel |
| Auth type  | Password, normal |

> The mailbox name is system-generated and found under **Webhosting → Datasheet** in your easyname Control Panel.  
> It is a short string, **not** your email address.

### CloudPit (Webhosting 2.0 / newer accounts)

| Setting    | Value |
|------------|-------|
| IMAP host  | `imap.easyname.com` |
| Port       | 993 (SSL) |
| Username   | Full **email address** (e.g. `you@yourdomain.at`) |
| Password   | Mailbox password from CloudPit |
| Auth type  | Password, normal (CRAM-MD5 is **not** supported) |

---

## Other provider notes

| Provider    | Note |
|-------------|------|
| Gmail       | Requires **App Password** when 2-Step Verification is on → [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) |
| Yahoo Mail  | Requires **App Password** → [login.yahoo.com/account/security](https://login.yahoo.com/account/security) |
| iCloud      | Requires **App-Specific Password** → [appleid.apple.com](https://appleid.apple.com) |
| Outlook/M365 | Basic-auth IMAP must be re-enabled by your tenant admin in the M365 Admin Center |

---

## Export formats

| Format  | Extension | Notes |
|---------|-----------|-------|
| MBOX    | `.mbox`   | Single file — import via Thunderbird (Tools → Import), Apple Mail, mutt |
| EML/ZIP | `.zip`    | One `.eml` per message in sub-folders mirroring the IMAP folder tree |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `[AUTHENTICATIONFAILED]` | Wrong username or password. For easyname Control Panel use the mailbox name from the Datasheet. |
| `Connection refused` | Check host/port; toggle SSL if using port 143 |
| Progress bar freezes | Normal — updates every 20 messages on slow connections |
| Outlook/M365 login fails | Ask admin to enable IMAP basic auth in M365 Admin Center |

---

## Project layout

```
mailexport/
├── rxconfig.py          # Reflex config (ports, app name)
├── requirements.txt     # Only 'reflex' — all other libs are Python stdlib
├── README.md            # This file
└── mailexport/
    ├── __init__.py
    ├── imap_utils.py    # IMAP helpers (connect, list, fetch)
    └── mailexport.py    # Reflex state + full UI incl. /help page
```

---

## License

MIT — do whatever you like with it.
