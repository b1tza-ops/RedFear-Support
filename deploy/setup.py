#!/usr/bin/env python3
"""Run from a checkout: sudo python3 deploy/setup.py. No credentials in argv."""
import getpass
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

TARGET = Path('/opt/redfear-support')
SOURCE = Path(__file__).resolve().parent.parent

def run(*args):
    subprocess.run(args, check=True)

def ask_id(label):
    while True:
        value = input(label + ': ').strip()
        if re.fullmatch(r'[1-9][0-9]{14,21}', value):
            return value
        print('Introdu un ID Discord numeric (Developer Mode → Copy ID).')

def collect():
    token = getpass.getpass('Token Discord (ascuns): ').strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]{30,200}', token):
        raise ValueError('Format token invalid; nu a fost salvat.')
    guild = ask_id('ID server RedFear')
    category = ask_id('ID categorie TICHETE')
    archive = ask_id('ID canal privat arhiva-tichete')
    roles = input('ID rol Moderator și opțional Helper, separate prin virgulă: ').strip()
    ids = [x.strip() for x in roles.split(',')]
    if not ids or any(not re.fullmatch(r'[1-9][0-9]{14,21}', x) or x == guild for x in ids):
        raise ValueError('ID-uri roluri invalide. Folosește doar roluri de staff.')
    return dict(DISCORD_TOKEN=token, GUILD_ID=guild, TICKET_CATEGORY_ID=category,
                ARCHIVE_CHANNEL_ID=archive, SUPPORT_ROLE_IDS=','.join(ids), DATA_DIR='data')

def main():
    if os.geteuid() != 0:
        raise SystemExit('Rulează: sudo python3 deploy/setup.py')
    if sys.version_info < (3, 11):
        raise SystemExit('Este necesar Python 3.11 sau mai nou.')
    if not Path('/run/systemd/system').exists():
        raise SystemExit('Acest instalator necesită systemd pe VPS.')
    if TARGET.is_symlink():
        raise SystemExit('Directorul destinație nu poate fi un link simbolic.')
    if (TARGET / '.env').exists():
        raise SystemExit('Există deja /opt/redfear-support/.env. Nu îl suprascriu. Pentru modificare: sudo nano /opt/redfear-support/.env, apoi sudo systemctl restart redfear-support')
    print('Configurează canalele private și rolurile de staff înainte de pornire.\nInstalare în /opt/redfear-support; serviciu redfear-support, pornire automată.')
    values = collect()
    try:
        account = pwd.getpwnam('redfear')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', str(TARGET), '--shell', '/usr/sbin/nologin', 'redfear')
        account = pwd.getpwnam('redfear')
    TARGET.mkdir(mode=0o750, exist_ok=True)
    os.chown(TARGET, account.pw_uid, account.pw_gid)
    for filename in ('bot.py', 'store.py', 'transcript.py', 'operations.py', 'fivem.py', 'applications.py', 'community.py', 'server_setup.py', 'requirements.txt'):
        dst = TARGET / filename
        if dst.is_symlink():
            raise ValueError('Fișier destinație nesigur')
        if (SOURCE / filename).resolve() != dst.resolve():
            shutil.copyfile(SOURCE / filename, dst)
        os.chown(dst, account.pw_uid, account.pw_gid)
    data = TARGET / 'data'
    if data.is_symlink():
        raise ValueError('Director data nesigur')
    data.mkdir(mode=0o700, exist_ok=True)
    os.chown(data, account.pw_uid, account.pw_gid)
    run('runuser', '-u', 'redfear', '--', sys.executable, '-m', 'venv', str(TARGET / '.venv'))
    run('runuser', '-u', 'redfear', '--', str(TARGET / '.venv/bin/pip'), 'install', '-r', str(TARGET / 'requirements.txt'))
    fd, tmp = tempfile.mkstemp(prefix='.env-', dir=TARGET)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
        os.chown(tmp, account.pw_uid, account.pw_gid)
        # Atomic creation; never overwrite an existing credential file.
        os.link(tmp, TARGET / '.env')
    finally:
        os.unlink(tmp)
    shutil.copyfile(SOURCE / 'deploy/redfear-support.service', '/etc/systemd/system/redfear-support.service')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', 'redfear-support')
    run('systemctl', 'restart', 'redfear-support')
    print('Serviciul a primit comanda de pornire. Verifică autentificarea Discord cu:\nsudo journalctl -u redfear-support -n 30 --no-pager\nDupă mesajul Conectat, execută /ticket_panel în #ajutor. Nu porni și bot.py manual.')

if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Instalarea s-a oprit: {exc}')
