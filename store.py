import sqlite3

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
        ''')
        self.db.commit()
    def active(self, owner):
        return self.db.execute("SELECT * FROM tickets WHERE owner=? AND status='open'", (owner,)).fetchone()
    def channel(self, channel):
        return self.db.execute('SELECT * FROM tickets WHERE channel=?', (channel,)).fetchone()
    def create(self, owner, channel, kind, created):
        with self.db:
            self.db.execute('INSERT INTO tickets(owner,channel,kind,created) VALUES(?,?,?,?)', (owner,channel,kind,created))
    def close(self, channel):
        with self.db:
            self.db.execute("UPDATE tickets SET status='closed' WHERE channel=?", (channel,))
    def claim(self, channel, staff):
        with self.db:
            return self.db.execute("UPDATE tickets SET claimed=? WHERE channel=? AND claimed IS NULL AND status='open'", (staff,channel)).rowcount == 1
