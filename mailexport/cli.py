"""
cli.py — Headless command-line interface.

Runs the exact same export / import logic as the web UI (via ``core.iter_export``
/ ``core.iter_import``) without a browser, so bulk migrations need no ad-hoc
scripts. Credentials come from flags or a ``.env`` file (``USER_MAIL`` /
``USER_PASSWORD``), and ``--host`` may be given directly or via ``--provider``.

Examples
--------
    # Export everything from a provider preset into ~/mail_export
    python -m mailexport export --provider "INWX / webspace.bz" --env --out ~/mail_export

    # Import an mbox/zip into a destination account (folders + flags restored)
    python -m mailexport import --host mail.webspace.bz --env --file ~/mail_export/mailbox_*.mbox
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import detect_format, iter_export, iter_import
from .ui_common import PROVIDERS


def _load_env(path: str) -> dict:
    creds: dict[str, str] = {}
    p = Path(path)
    if p.is_file():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def _resolve_creds(args) -> tuple[str, str]:
    user, pw = args.user, args.password
    if args.env:
        env = _load_env(args.env)
        user = user or env.get("USER_MAIL")
        pw = pw or env.get("USER_PASSWORD")
    if not user or not pw:
        sys.exit("Error: provide --user/--password, or --env with USER_MAIL/USER_PASSWORD.")
    return user, pw


def _resolve_host(args) -> str:
    if args.host:
        return args.host
    if args.provider:
        if args.provider not in PROVIDERS:
            sys.exit(f"Error: unknown provider {args.provider!r}. Known: {', '.join(PROVIDERS)}")
        return PROVIDERS[args.provider][0]
    sys.exit("Error: provide --host or --provider.")


def _print_progress(label: str, p) -> None:
    line = f"  {label}: {p.done}/{p.total}"
    if p.ok or p.failed:
        line += f"  ok={p.ok} fail={p.failed}"
    if p.info:
        line += f"  {p.info}"
    print(line, flush=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        prog="python -m mailexport",
        description="IMAP mailbox export / import over IMAP (no webmail upload-size limit).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", help="IMAP host, e.g. mail.webspace.bz")
    common.add_argument("--provider", help=f"Preset host. One of: {', '.join(PROVIDERS)}")
    common.add_argument("--port", default="993")
    common.add_argument("--user", help="Username / full email address")
    common.add_argument("--password", help="Password (prefer --env to keep it out of shell history)")
    common.add_argument("--env", nargs="?", const=".env", metavar="PATH",
                        help="Read USER_MAIL/USER_PASSWORD from a .env file (default: ./.env)")
    common.add_argument("--no-ssl", action="store_true", help="Disable SSL/TLS (use port 143)")

    pe = sub.add_parser("export", parents=[common], help="Export folders to .mbox or .zip")
    pe.add_argument("--out", required=True, help="Output directory")
    pe.add_argument("--format", choices=["mbox", "eml_zip"], default="mbox")
    pe.add_argument("--folders", help="Comma-separated folder names to export (default: all)")

    pi = sub.add_parser("import", parents=[common], help="Import a .mbox/.zip into a mailbox")
    pi.add_argument("--file", required=True, help="Path to the .mbox or .zip to import")
    pi.add_argument("--dest-folder", default="INBOX",
                    help="Fallback folder for files without folder metadata (default: INBOX)")
    pi.add_argument("--no-structure", action="store_true",
                    help="ZIP only: import everything into --dest-folder instead of per-subfolder")
    pi.add_argument("--prefix", default="",
                    help="Folder namespace prefix to force (e.g. 'INBOX.'); auto-detected if omitted")

    args = ap.parse_args(argv)
    user, pw = _resolve_creds(args)
    host = _resolve_host(args)
    ssl = not args.no_ssl

    last = None
    try:
        if args.cmd == "export":
            folders = [f.strip() for f in args.folders.split(",")] if args.folders else None
            print(f"Exporting from {host} as {user} → {args.out} ({args.format})…", flush=True)
            for p in iter_export(host=host, port=args.port, username=user, password=pw, ssl=ssl,
                                 out_dir=args.out, export_fmt=args.format, folders=folders):
                last = p
                if p.finished or p.total == 0 or p.done % 50 == 0:
                    _print_progress("export", p)
            print(f"\n✅ Exported {last.done} message(s) → {last.out_path}")
        else:
            fmt = detect_format(args.file)
            print(f"Importing {args.file} → {host} as {user}…", flush=True)
            for p in iter_import(host=host, port=args.port, username=user, password=pw, ssl=ssl,
                                 src_path=args.file, fmt=fmt, dest_folder=args.dest_folder,
                                 preserve_structure=not args.no_structure, folder_prefix=args.prefix):
                last = p
                if p.finished or p.total == 0 or p.done % 50 == 0:
                    _print_progress("import", p)
            print(f"\n✅ Imported {last.ok}/{last.total}; {last.failed} failed.")
            if last.per_folder:
                print(f"   per-folder: {last.per_folder}")
            if last.first_error:
                print(f"   first error: {last.first_error}")
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
    except Exception as exc:
        sys.exit(f"\n❌ {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
