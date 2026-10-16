import sqlite3
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tickets (
          id INTEGER PRIMARY KEY, owner INTEGER NOT NULL, channel INTEGER UNIQUE,
          kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', claimed INTEGER,
          created REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active ON tickets(owner) WHERE status='open';
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS participants (channel INTEGER, member INTEGER,
          PRIMARY KEY(channel, member));
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, channel INTEGER,
          action TEXT NOT NULL, actor INTEGER, created REAL NOT NULL, detail TEXT NOT NULL);
        ''')
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(tickets)')}
        for name, definition in [('closed_at', 'REAL'), ('closed_by', 'INTEGER'), ('reason', 'TEXT')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE tickets ADD COLUMN {name} {definition}')
        self.db.commit()

    def active(self, owner):
        return self.db.execute("SELECT * FROM tickets WHERE owner=? AND status='open'", (owner,)).fetchone()

    def channel(self, channel):
        return self.db.execute('SELECT * FROM tickets WHERE channel=?', (channel,)).fetchone()

    def next_number(self):
        # Persist the counter before network calls. Gaps are intentional; numbers never repeat.
        with self.db:
            maximum = self.db.execute('SELECT COALESCE(MAX(id),0) FROM tickets').fetchone()[0]
            current = int(self.setting('counter') or 0)
            value = max(current, maximum) + 1
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('counter', str(value)))
        return value

    def create(self, owner, channel, kind, created, number=None):
        with self.db:
            cursor = self.db.execute('INSERT INTO tickets(id,owner,channel,kind,created) VALUES(?,?,?,?,?)',
                                    (number, owner, channel, kind, created))
        return cursor.lastrowid

    def close(self, channel, actor=None, reason=''):
        with self.db:
            changed = self.db.execute("UPDATE tickets SET status='closed',closed_at=?,closed_by=?,reason=? WHERE channel=? AND status='open'",
                                     (time.time(), actor, reason, channel)).rowcount
            if changed:
                self._event(channel, 'close', actor, reason)
        return bool(changed)

    def reopen(self, channel, actor):
        with self.db:
            # The unique index also prevents reopening over another active ticket.
            changed = self.db.execute("UPDATE tickets SET status='open',claimed=NULL WHERE channel=? AND status='closed'", (channel,)).rowcount
            if changed:
                self._event(channel, 'reopen', actor, '')
        return bool(changed)

    def claim(self, channel, staff):
        with self.db:
            changed = self.db.execute("UPDATE tickets SET claimed=? WHERE channel=? AND claimed IS NULL AND status='open'", (staff,channel)).rowcount
            if changed:
                self._event(channel, 'claim', staff, '')
        return bool(changed)

    def _event(self, channel, action, actor, detail):
        self.db.execute('INSERT INTO events(channel,action,actor,created,detail) VALUES(?,?,?,?,?)',
                        (channel, action, actor, time.time(), detail))

    def event(self, channel, action, actor, detail=''):
        with self.db:
            self._event(channel, action, actor, detail)

    def participants(self, channel):
        return [r[0] for r in self.db.execute('SELECT member FROM participants WHERE channel=?', (channel,))]

    def participant(self, channel, member, add):
        with self.db:
            if add:
                self.db.execute('INSERT OR IGNORE INTO participants VALUES(?,?)', (channel, member))
            else:
                self.db.execute('DELETE FROM participants WHERE channel=? AND member=?', (channel,member))

    def setting(self, key):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

    def stats(self):
        counts = dict(self.db.execute('SELECT status,COUNT(*) FROM tickets GROUP BY status'))
        staff = self.db.execute("SELECT actor,SUM(action='claim') AS claims,SUM(action='close') AS closes FROM events WHERE actor IS NOT NULL GROUP BY actor ORDER BY closes DESC,claims DESC").fetchall()
        return counts, staff
