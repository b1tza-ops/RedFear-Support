import secrets
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
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS blocked (member INTEGER PRIMARY KEY, actor INTEGER NOT NULL,
          reason TEXT NOT NULL, expires REAL);
        CREATE TABLE IF NOT EXISTS feedback_invites (token TEXT PRIMARY KEY, channel INTEGER NOT NULL,
          cycle INTEGER NOT NULL, owner INTEGER NOT NULL, staff INTEGER, expires REAL NOT NULL,
          UNIQUE(channel,cycle));
        CREATE TABLE IF NOT EXISTS feedback (channel INTEGER, cycle INTEGER, owner INTEGER NOT NULL,
          staff INTEGER, score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5), created REAL NOT NULL,
          PRIMARY KEY(channel,cycle));
        ''')
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(tickets)')}
        for name, definition in [('closed_at', 'REAL'), ('closed_by', 'INTEGER'), ('reason', 'TEXT'), ('priority', "TEXT NOT NULL DEFAULT 'normal'"),
                                 ('last_activity', 'REAL'), ('warned_at', 'REAL'),
                                 ('close_cycle', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE tickets ADD COLUMN {name} {definition}')
        self.db.execute('UPDATE tickets SET last_activity=created WHERE last_activity IS NULL')
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
            cursor = self.db.execute('INSERT INTO tickets(id,owner,channel,kind,created,last_activity) VALUES(?,?,?,?,?,?)',
                                    (number, owner, channel, kind, created, created))
        return cursor.lastrowid

    def close(self, channel, actor=None, reason=''):
        with self.db:
            changed = self.db.execute("UPDATE tickets SET status='closed',closed_at=?,closed_by=?,reason=?,close_cycle=close_cycle+1,warned_at=NULL WHERE channel=? AND status='open'",
                                     (time.time(), actor, reason, channel)).rowcount
            if changed:
                self._event(channel, 'close', actor, reason)
        return bool(changed)

    def reopen(self, channel, actor):
        with self.db:
            # The unique index also prevents reopening over another active ticket.
            changed = self.db.execute("UPDATE tickets SET status='open',claimed=NULL,last_activity=?,warned_at=NULL WHERE channel=? AND status='closed'", (time.time(), channel)).rowcount
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


    def assign(self, channel, actor, target, admin=False):
        with self.db:
            row = self.channel(channel)
            if not row or row['status'] != 'open' or row['claimed'] is None:
                raise ValueError('Tichetul nu este preluat sau nu este deschis.')
            if row['claimed'] != actor and not admin:
                raise ValueError('Doar moderatorul care a preluat tichetul sau un administrator îl poate transfera/elibera.')
            self.db.execute('UPDATE tickets SET claimed=? WHERE channel=?', (target, channel))
            self._event(channel, 'release' if target is None else 'transfer', actor, str(target or ''))

    def set_priority(self, channel, value, actor):
        if value not in ('normal', 'high', 'urgent'):
            raise ValueError('Prioritate invalidă.')
        with self.db:
            self.db.execute("UPDATE tickets SET priority=? WHERE channel=? AND status='open'", (value, channel))
            self._event(channel, 'priority', actor, value)

    def block(self, member, actor, reason, expires=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO blocked VALUES(?,?,?,?)', (member, actor, reason, expires))
            self._event(None, 'block', actor, str(member))

    def unblock(self, member, actor):
        with self.db:
            self.db.execute('DELETE FROM blocked WHERE member=?', (member,))
            self._event(None, 'unblock', actor, str(member))

    def blocked(self, member, now=None):
        now = time.time() if now is None else now
        return self.db.execute('SELECT * FROM blocked WHERE member=? AND (expires IS NULL OR expires>?)', (member,now)).fetchone()

    def touch(self, channel, when):
        with self.db:
            self.db.execute("UPDATE tickets SET last_activity=?,warned_at=NULL WHERE channel=? AND status='open' AND last_activity<?", (when,channel,when))

    def open_tickets(self):
        return self.db.execute("SELECT * FROM tickets WHERE status='open'").fetchall()

    def warn(self, channel, when):
        with self.db:
            self.db.execute("UPDATE tickets SET warned_at=? WHERE channel=? AND status='open'", (when,channel))

    def configure_idle(self, warn_seconds, close_seconds):
        if warn_seconds < 3600 or (close_seconds and close_seconds <= warn_seconds):
            raise ValueError('Închiderea trebuie să fie după avertizare; 0 dezactivează închiderea.')
        with self.db:
            for key,value in [('idle_warn',warn_seconds),('idle_close',close_seconds)]:
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key,str(value)))
            self.db.execute("UPDATE tickets SET warned_at=NULL WHERE status='open'")

    def invite_feedback(self, channel, now=None):
        now = time.time() if now is None else now
        row = self.channel(channel)
        if not row or row['status'] != 'closed':
            raise ValueError('Feedback disponibil numai pentru un tichet închis.')
        token = secrets.token_urlsafe(18)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO feedback_invites VALUES(?,?,?,?,?,?)',
                            (token,channel,row['close_cycle'],row['owner'],row['claimed'],now+30*86400))
        return self.db.execute('SELECT token FROM feedback_invites WHERE channel=? AND cycle=?', (channel,row['close_cycle'])).fetchone()[0]

    def pending_feedback(self, now=None):
        now = time.time() if now is None else now
        return self.db.execute("""SELECT f.* FROM feedback_invites f JOIN tickets t ON t.channel=f.channel
          WHERE f.expires>? AND t.status='closed' AND t.close_cycle=f.cycle
          AND NOT EXISTS(SELECT 1 FROM feedback r WHERE r.channel=f.channel AND r.cycle=f.cycle)""", (now,)).fetchall()

    def rate(self, token, owner, score, now=None):
        now = time.time() if now is None else now
        if score not in range(1,6):
            raise ValueError('Alege între 1 și 5 stele.')
        with self.db:
            invite = self.db.execute('SELECT * FROM feedback_invites WHERE token=? AND owner=? AND expires>?', (token,owner,now)).fetchone()
            if not invite:
                raise ValueError('Evaluarea nu îți aparține sau a expirat.')
            row = self.channel(invite['channel'])
            if not row or row['status'] != 'closed' or row['close_cycle'] != invite['cycle']:
                raise ValueError('Tichetul a fost redeschis; această evaluare nu mai este disponibilă.')
            try:
                self.db.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?)', (invite['channel'],invite['cycle'],owner,invite['staff'],score,now))
            except sqlite3.IntegrityError:
                raise ValueError('Ai evaluat deja această închidere.')

    def feedback_stats(self):
        return self.db.execute('SELECT staff,COUNT(*) AS count,AVG(score) AS average FROM feedback GROUP BY staff').fetchall()
