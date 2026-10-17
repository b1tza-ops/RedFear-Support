import json
import secrets
import sqlite3
import time
from casino_store import CasinoStore


class Store(CasinoStore):
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
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS helper_applications (
          id INTEGER PRIMARY KEY, owner INTEGER NOT NULL, answers TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
          review_channel INTEGER NOT NULL, reviewer_role INTEGER NOT NULL,
          message INTEGER, reviewed_at REAL, reviewed_by INTEGER, reason TEXT,
          dm_state INTEGER NOT NULL DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_helper
          ON helper_applications(owner) WHERE status='pending';
        ''')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS moderation_cases (
          id INTEGER PRIMARY KEY, actor INTEGER NOT NULL, target INTEGER NOT NULL,
          action TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', detail TEXT NOT NULL DEFAULT '',
          until_time REAL, log_state INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS moderation_target ON moderation_cases(target,id);
        CREATE TABLE IF NOT EXISTS moderation_purge_messages (
          case_id INTEGER NOT NULL, message INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          PRIMARY KEY(case_id,message));
        ''')
        helper_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(helper_applications)')}
        if 'kind' not in helper_columns:
            self.db.execute("ALTER TABLE helper_applications ADD COLUMN kind TEXT NOT NULL DEFAULT 'helper'")
        self.interview_schema()
        self.casino_schema()
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


    def helper_latest(self, owner):
        return self.db.execute('SELECT * FROM helper_applications WHERE owner=? ORDER BY id DESC LIMIT 1',(owner,)).fetchone()

    def helper_application(self, number):
        return self.db.execute('SELECT * FROM helper_applications WHERE id=?',(number,)).fetchone()

    def helper_card(self, channel, message):
        return self.db.execute('SELECT * FROM helper_applications WHERE review_channel=? AND message=?',(channel,message)).fetchone()

    def helper_eligible(self, owner, cooldown, now=None):
        now=time.time() if now is None else now
        pending=self.db.execute("SELECT id FROM helper_applications WHERE owner=? AND status='pending'",(owner,)).fetchone()
        if pending:
            raise ValueError(f'Ai deja aplicația #{pending["id"]} în așteptare.')
        last=self.helper_latest(owner)
        if last and now-last['created'] < 60:
            raise ValueError('Așteaptă un minut înainte să trimiți din nou.')
        decision=self.db.execute("SELECT MAX(reviewed_at) FROM helper_applications WHERE owner=? AND status IN ('accepted','rejected')",(owner,)).fetchone()[0]
        if decision is not None and now < decision+cooldown:
            raise ValueError('Perioada de așteptare pentru o nouă aplicație nu s-a încheiat.')

    def helper_create(self, owner, answers, channel, role, cooldown, now=None, kind="helper"):
        if kind not in ("helper", "moderator", "discord", "support", "moderation"):
            raise ValueError("Funcție necunoscută.")
        now=time.time() if now is None else now
        with self.db:
            self.helper_eligible(owner,cooldown,now)
            cursor=self.db.execute('INSERT INTO helper_applications(owner,answers,created,review_channel,reviewer_role,kind) VALUES(?,?,?,?,?,?)',
                                   (owner,answers,now,channel,role,kind))
        return cursor.lastrowid

    def helper_set_message(self, number, message):
        with self.db:
            self.db.execute('UPDATE helper_applications SET message=? WHERE id=?',(message,number))

    def helper_delivery_failed(self, number):
        with self.db:
            self.db.execute("UPDATE helper_applications SET status='failed' WHERE id=? AND status='pending' AND message IS NULL",(number,))

    def helper_reviewer_role(self, channel, role):
        with self.db:
            self.db.execute("UPDATE helper_applications SET reviewer_role=? WHERE review_channel=?",(role,channel))

    def helper_decide(self, number, actor, status, reason, now=None):
        if status not in ('accepted','rejected') or not 3 <= len(reason) <= 1000:
            raise ValueError('Decizie sau motiv invalid.')
        with self.db:
            row=self.helper_application(number)
            if not row or row['status'] != 'pending' or row['message'] is None:
                raise ValueError('Aplicația nu mai este în așteptare sau nu a fost publicată.')
            if row['owner'] == actor:
                raise ValueError('Nu poți evalua propria aplicație.')
            self.db.execute("UPDATE helper_applications SET status=?,reviewed_at=?,reviewed_by=?,reason=? WHERE id=? AND status='pending'",
                (status,time.time() if now is None else now,actor,reason,number))
        return self.helper_application(number)

    def helper_dm(self, number, state):
        with self.db:
            self.db.execute('UPDATE helper_applications SET dm_state=? WHERE id=?',(state,number))

    def moderation_create(self, actor, target, action, reason, until=None):
        with self.db:
            cursor=self.db.execute('INSERT INTO moderation_cases(actor,target,action,reason,created,until_time) VALUES(?,?,?,?,?,?)',
                                   (actor,target,action,reason,time.time(),until))
        return cursor.lastrowid

    def moderation_case(self, number):
        return self.db.execute('SELECT * FROM moderation_cases WHERE id=?',(number,)).fetchone()

    def moderation_finish(self, number, status, detail=''):
        if status not in ('applied','failed','uncertain','partial'):
            raise ValueError('Stare invalidă pentru cazul de moderare.')
        with self.db:
            self.db.execute("UPDATE moderation_cases SET status=?,detail=? WHERE id=? AND status='pending'",(status,detail,number))

    def moderation_log_state(self, number, state):
        with self.db:
            self.db.execute('UPDATE moderation_cases SET log_state=? WHERE id=?',(state,number))

    def moderation_history(self, target, page=1, warnings_only=False):
        if page < 1:
            raise ValueError('Pagina trebuie să fie pozitivă.')
        condition=" AND action='warn' AND status='applied'" if warnings_only else ''
        return self.db.execute('SELECT * FROM moderation_cases WHERE target=?'+condition+' ORDER BY id DESC LIMIT 5 OFFSET ?',
                               (target,(page-1)*5)).fetchall()

    def moderation_revoke_warn(self, number, actor, reason):
        reason=reason.strip()
        if not 3 <= len(reason) <= 500:
            raise ValueError('Motiv obligatoriu de 3–500 caractere.')
        with self.db:
            row=self.moderation_case(number)
            if not row or row['action']!='warn' or row['status']!='applied':
                raise ValueError('Cazul nu este un avertisment activ sau a fost deja retras.')
            cursor=self.db.execute("INSERT INTO moderation_cases(actor,target,action,reason,created,status,detail) VALUES(?,?,'warn_revoke',?,?,'applied',?)",
                (actor,row['target'],reason,time.time(),f'Avertisment retras: #{number}.'))
            audit=cursor.lastrowid
            self.db.execute("UPDATE moderation_cases SET status='revoked',detail=? WHERE id=?",(f'Retras prin cazul #{audit}.',number))
        return self.moderation_case(audit)

    def moderation_purge_create(self, actor, channel, messages, reason):
        if not 1<=len(messages)<=50 or len(set(messages))!=len(messages):
            raise ValueError('Selecție invalidă pentru ștergere.')
        with self.db:
            cursor=self.db.execute("INSERT INTO moderation_cases(actor,target,action,reason,created) VALUES(?,?,'purge',?,?)",(actor,channel,reason,time.time()))
            number=cursor.lastrowid
            self.db.executemany('INSERT INTO moderation_purge_messages(case_id,message) VALUES(?,?)',[(number,m) for m in messages])
        return number

    def moderation_purge_update(self, number, message, status):
        if status not in ('deleted','skipped','uncertain'):
            raise ValueError('Stare invalidă pentru mesaj.')
        with self.db:
            self.db.execute('UPDATE moderation_purge_messages SET status=? WHERE case_id=? AND message=?',(status,number,message))

    def moderation_purge_items(self, number):
        return self.db.execute('SELECT * FROM moderation_purge_messages WHERE case_id=? ORDER BY message',(number,)).fetchall()


    def interview_schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS staff_interviews(
          application INTEGER PRIMARY KEY, candidate INTEGER NOT NULL, scheduled REAL NOT NULL,
          duration INTEGER NOT NULL, location TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
          revision INTEGER NOT NULL DEFAULT 1, message INTEGER, request TEXT NOT NULL DEFAULT '',
          updated REAL NOT NULL, result TEXT, dm_state INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS staff_interview_events(
          id INTEGER PRIMARY KEY, application INTEGER NOT NULL, actor INTEGER NOT NULL,
          event TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS staff_trial(
          application INTEGER PRIMARY KEY, candidate INTEGER NOT NULL, responsible INTEGER NOT NULL,
          started REAL NOT NULL, ends REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          result_reason TEXT, decided_by INTEGER, decided REAL);
        CREATE TABLE IF NOT EXISTS staff_reminders(
          application INTEGER NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL,
          recipient INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'attempted', created REAL NOT NULL,
          PRIMARY KEY(application,revision,kind,recipient));
        ''')

    def interview(self, application):
        return self.db.execute('SELECT * FROM staff_interviews WHERE application=?',(application,)).fetchone()

    def interview_event(self, application, actor, event, detail):
        self.db.execute('INSERT INTO staff_interview_events(application,actor,event,detail,created) VALUES(?,?,?,?,?)',
                        (application,actor,event,detail,time.time()))

    def interview_schedule(self, application, actor, scheduled, duration, location):
        with self.db:
            row=self.helper_application(application)
            old=self.interview(application)
            if not row or row['status']!='pending' or row['owner']==actor or (old and old['result']):
                raise ValueError('Alege o aplicație în așteptare, diferită de propria aplicație.')
            self.db.execute('''INSERT INTO staff_interviews(application,candidate,scheduled,duration,location,updated)
                VALUES(?,?,?,?,?,?) ON CONFLICT(application) DO UPDATE SET scheduled=excluded.scheduled,
                duration=excluded.duration,location=excluded.location,status='scheduled',revision=revision+1,
                message=NULL,request='',updated=excluded.updated,dm_state=0''',
                (application,row['owner'],scheduled,duration,location,time.time()))
            self.interview_event(application,actor,'scheduled',json_interview(scheduled,duration,location))
        return self.interview(application)

    def interview_message(self, application, revision, message, state):
        with self.db:
            self.db.execute('UPDATE staff_interviews SET message=?,dm_state=? WHERE application=? AND revision=?',
                            (message,state,application,revision))

    def interview_reply(self, application, actor, message, revision, action, reason=''):
        if action not in ('confirmed','reschedule') or (action=='reschedule' and not 3<=len(reason.strip())<=500):
            raise ValueError('Răspuns sau motiv invalid.')
        with self.db:
            row=self.interview(application)
            app=self.helper_application(application)
            if (not row or not app or app['status']!='pending' or row['candidate']!=actor or row['message']!=message
                    or row['revision']!=revision or row['status'] not in ('scheduled','confirmed') or row['scheduled']<=time.time()):
                raise ValueError('Invitație expirată, modificată sau care nu îți aparține.')
            if row['status']==action:
                raise ValueError('Răspunsul a fost deja salvat.')
            self.db.execute('UPDATE staff_interviews SET status=?,request=?,updated=? WHERE application=?',
                            (action,reason,time.time(),application))
            self.interview_event(application,actor,action,reason)
        return self.interview(application)

    def trial(self, application):
        return self.db.execute('SELECT * FROM staff_trial WHERE application=?',(application,)).fetchone()

    def interview_result(self, application, actor, result, reason, responsible=None, days=14):
        if result not in ('accepted','rejected','trial') or not 3<=len(reason.strip())<=1000:
            raise ValueError('Rezultat sau motiv invalid.')
        if result=='trial' and (not responsible or not 1<=days<=90):
            raise ValueError('Pentru probă alege responsabilul și 1–90 zile.')
        now=time.time()
        with self.db:
            row=self.interview(application)
            app=self.helper_application(application)
            if not row or row['result'] or not app or app['status']!='pending' or app['owner']==actor:
                raise ValueError('Interviul nu există, este deja finalizat sau aplicația nu mai este în așteptare.')
            if result=='trial' and responsible==app['owner']:
                raise ValueError('Candidatul nu poate fi propriul responsabil.')
            self.db.execute("UPDATE helper_applications SET status=?,reviewed_at=?,reviewed_by=?,reason=?,dm_state=0 WHERE id=? AND status='pending'",
                            ('rejected' if result=='rejected' else 'accepted',now,actor,reason,application))
            self.db.execute("UPDATE staff_interviews SET status='completed',result=?,updated=? WHERE application=?",(result,now,application))
            if result=='trial':
                self.db.execute('INSERT INTO staff_trial(application,candidate,responsible,started,ends) VALUES(?,?,?,?,?)',
                                (application,app['owner'],responsible,now,now+days*86400))
            self.interview_event(application,actor,'result:'+result,reason)
        return self.helper_application(application)

    def trial_note(self, application, actor, note, owner_id):
        if not 3<=len(note.strip())<=1000:
            raise ValueError('Observația trebuie să aibă 3–1000 caractere.')
        with self.db:
            row=self.trial(application)
            if not row or row['status']!='active' or actor not in (owner_id,row['responsible']):
                raise ValueError('Doar proprietarul și responsabilul pot adăuga observații la proba activă.')
            self.interview_event(application,actor,'trial-note',note)

    def trial_finish(self, application, actor, result, reason):
        if result not in ('passed','failed') or not 3<=len(reason.strip())<=1000:
            raise ValueError('Rezultat sau motiv invalid.')
        with self.db:
            row=self.trial(application)
            if not row or row['status']!='active' or actor==row['candidate']:
                raise ValueError('Proba nu există, este finalizată sau nu îți poți evalua propria probă.')
            self.db.execute('UPDATE staff_trial SET status=?,result_reason=?,decided_by=?,decided=? WHERE application=?',
                            (result,reason,actor,time.time(),application))
            self.interview_event(application,actor,'trial:'+result,reason)
        return self.trial(application)

    def reminder_reserve(self, application, revision, kind, recipient):
        with self.db:
            cur=self.db.execute('INSERT OR IGNORE INTO staff_reminders(application,revision,kind,recipient,created) VALUES(?,?,?,?,?)',
                                (application,revision,kind,recipient,time.time()))
        return cur.rowcount==1

    def reminder_finish(self, application, revision, kind, recipient, state):
        with self.db:
            self.db.execute('UPDATE staff_reminders SET state=? WHERE application=? AND revision=? AND kind=? AND recipient=?',
                            (state,application,revision,kind,recipient))


def json_interview(scheduled, duration, location):
    return json.dumps(dict(scheduled=scheduled, duration=duration, location=location), ensure_ascii=False)
