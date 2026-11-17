"""Private local reminder setup. Configuration stays outside the Git repository."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path.home() / '.config' / 'studyflow' / 'reminders.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['configure', 'check', 'status', 'start'])
    parser.add_argument('--database', type=Path, help='Existing SQLite database used by your web app')
    args = parser.parse_args()
    if args.action == 'configure':
        if not args.database or not args.database.expanduser().resolve().is_file():
            parser.error('--database must point to the existing web app SQLite database')
        if not sys.stdin.isatty():
            parser.error('Run configure in your own terminal so the API key can be entered privately')
        key = getpass.getpass('Resend API key (hidden): ').strip()
        if not key:
            parser.error('An API key is required')
        sender = input('Sender [StudyFlow <onboarding@resend.dev>]: ').strip()
        data = {
            'RESEND_API_KEY': key,
            'STUDYFLOW_REMINDER_FROM': sender or 'StudyFlow <onboarding@resend.dev>',
            'STUDYFLOW_DATABASE_URI': 'sqlite:///' + str(args.database.expanduser().resolve()),
        }
        CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Atomic replacement keeps an existing configuration intact on write failure.
        fd, temporary = tempfile.mkstemp(dir=CONFIG.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream)
            os.replace(temporary, CONFIG)  # mkstemp creates the file with owner-only access.
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print('Private configuration saved. No email sent and no worker started.')
        print('The default test sender can send only to your Resend account email.')
        return
    if not CONFIG.is_file():
        parser.error('Run configure first; see docs/EMAIL_REMINDERS.md')
    if CONFIG.stat().st_mode & 0o077:
        parser.error('Configuration must be private: chmod 600 ~/.config/studyflow/reminders.json')
    try:
        data = json.loads(CONFIG.read_text())
        names = ('RESEND_API_KEY', 'STUDYFLOW_REMINDER_FROM', 'STUDYFLOW_DATABASE_URI')
        if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k].strip() for k in names):
            raise ValueError()
    except (ValueError, OSError):
        parser.error('Invalid configuration; run configure again')
    env = os.environ.copy()
    env.update({k: data[k] for k in names})
    command = {'check': ['reminders-check'], 'status': ['reminders-status'],
               'start': ['reminders', '--loop', '--interval', '60']}[args.action]
    try:
        result = subprocess.run([sys.executable, '-m', 'flask', '--app', 'app', *command], cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
