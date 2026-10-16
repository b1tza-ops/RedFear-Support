#!/usr/bin/env python3
"""Update the existing service without touching .env; back up code and SQLite."""
import datetime
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parent.parent
TARGET = Path('/opt/redfear-support')
FILES = ('bot.py', 'store.py', 'transcript.py', 'operations.py', 'fivem.py', 'requirements.txt')


def run(*args):
    subprocess.run(args, check=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Rulează sudo python3 deploy/update.py din repository.')
    if TARGET.is_symlink() or not (TARGET / '.env').is_file():
        raise SystemExit('Instalare existentă negăsită sau destinație nesigură.')
    if SOURCE == TARGET:
        raise SystemExit('Rulează din checkout-ul GitHub, separat de /opt/redfear-support.')
    account = pwd.getpwnam('redfear')
    for name in FILES:
        if not (SOURCE / name).is_file() or (TARGET / name).is_symlink():
            raise SystemExit('Fișier absent sau destinație nesigură: ' + name)
    # Syntax check without writing caches into the checkout.
    for name in FILES:
        if name.endswith('.py'):
            compile((SOURCE / name).read_text(), name, 'exec')
    run('systemctl', 'stop', 'redfear-support')
    backup = None
    existed = set()
    try:
        root = TARGET / 'backups'
        if root.is_symlink():
            raise ValueError('Director backup nesigur')
        root.mkdir(mode=0o700, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
        backup = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
        for name in FILES:
            if (TARGET / name).exists():
                shutil.copy2(TARGET / name, backup / name)
                existed.add(name)
        db = TARGET / 'data/tickets.sqlite3'
        if db.exists():
            with sqlite3.connect(db) as source, sqlite3.connect(backup / 'tickets.sqlite3') as dest:
                source.backup(dest)
        # Dependencies are unchanged in this release. Use the existing environment.
        run(str(TARGET / '.venv/bin/python'), '-c', 'import discord, dotenv, aiohttp')
        for name in FILES:
            fd, temporary = tempfile.mkstemp(prefix='.update-', dir=TARGET)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write((SOURCE / name).read_bytes())
                os.chown(temporary, account.pw_uid, account.pw_gid)
                os.chmod(temporary, 0o640)
                os.replace(temporary, TARGET / name)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        run('systemctl', 'start', 'redfear-support')
    except Exception:
        if backup:
            for name in FILES:
                if name in existed:
                    shutil.copy2(backup / name, TARGET / name)
                    os.chown(TARGET / name, account.pw_uid, account.pw_gid)
        run('systemctl', 'start', 'redfear-support')
        raise
    print(f'Cod actualizat. Backup: {backup}\n.env și datele existente au fost păstrate.\nVerifică pornirea: sudo journalctl -u redfear-support -n 40 --no-pager\nÎn Discord: /ticket_categories, apoi /ticket_controls în tichetele vechi dacă este necesar.')


if __name__ == '__main__':
    main()
