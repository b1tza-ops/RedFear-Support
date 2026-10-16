"""Transactional, play-token-only Blackjack. No external economy or payments."""
import json
import secrets
import time

SUITS = '♠♥♦♣'
RANKS = ['A','2','3','4','5','6','7','8','9','10','J','Q','K']


def card_text(card):
    return RANKS[card % 13] + SUITS[card // 13]


def total(hand):
    value=sum(11 if c%13==0 else min(c%13+1,10) for c in hand)
    aces=sum(c%13==0 for c in hand)
    while value>21 and aces:
        value-=10; aces-=1
    return value


def natural(hand):
    return len(hand)==2 and total(hand)==21


class CasinoStore:
    def casino_schema(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS casino_accounts (
          member INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 1000 CHECK(balance>=0),
          daily REAL, wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
          pushes INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS casino_games (
          id INTEGER PRIMARY KEY, channel INTEGER NOT NULL, message INTEGER,
          owner INTEGER NOT NULL, bet INTEGER NOT NULL, phase TEXT NOT NULL,
          state TEXT NOT NULL, deadline REAL NOT NULL, created REAL NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS casino_one_table ON casino_games(channel)
          WHERE phase IN ('lobby','playing');
        CREATE TABLE IF NOT EXISTS casino_seats (
          member INTEGER PRIMARY KEY, game INTEGER NOT NULL, stake INTEGER NOT NULL);
        ''')

    def _casino_account(self,member):
        self.db.execute('INSERT OR IGNORE INTO casino_accounts(member) VALUES(?)',(member,))

    def casino_account(self,member):
        with self.db:
            self._casino_account(member)
        return self.db.execute('SELECT * FROM casino_accounts WHERE member=?',(member,)).fetchone()

    def casino_daily(self,member,now=None):
        now=time.time() if now is None else now
        with self.db:
            self._casino_account(member)
            row=self.db.execute('SELECT daily FROM casino_accounts WHERE member=?',(member,)).fetchone()
            if row['daily'] is not None and now<row['daily']+86400:
                raise ValueError('Bonusul este disponibil o dată la 24 de ore.')
            self.db.execute('UPDATE casino_accounts SET balance=balance+500,daily=? WHERE member=?',(now,member))
        return self.casino_account(member)

    def casino_top(self):
        return self.db.execute('SELECT * FROM casino_accounts ORDER BY balance DESC,member LIMIT 10').fetchall()

    def casino_game(self,number):
        return self.db.execute('SELECT * FROM casino_games WHERE id=?',(number,)).fetchone()

    def casino_card(self,channel,message):
        return self.db.execute('SELECT * FROM casino_games WHERE channel=? AND message=?',(channel,message)).fetchone()

    def casino_active(self):
        return self.db.execute("SELECT * FROM casino_games WHERE phase IN ('lobby','playing') ORDER BY id").fetchall()

    def _casino_save(self,number,state,phase,deadline):
        self.db.execute('UPDATE casino_games SET state=?,phase=?,deadline=?,revision=revision+1 WHERE id=?',
                        (json.dumps(state),phase,deadline,number))

    def _casino_reserve(self,member,number,stake):
        self._casino_account(member)
        if self.db.execute('SELECT 1 FROM casino_seats WHERE member=?',(member,)).fetchone():
            raise ValueError('Ești deja la o masă activă.')
        balance=self.db.execute('SELECT balance FROM casino_accounts WHERE member=?',(member,)).fetchone()[0]
        if balance<stake:
            raise ValueError('Jetoane insuficiente. Vezi /casino_daily sau /casino_profile.')
        self.db.execute('UPDATE casino_accounts SET balance=balance-? WHERE member=?',(stake,member))
        self.db.execute('INSERT INTO casino_seats VALUES(?,?,?)',(member,number,stake))

    def casino_create(self,channel,owner,bet,now=None):
        if not isinstance(bet,int) or not 10<=bet<=500 or bet%10:
            raise ValueError('Miza trebuie să fie între 10 și 500, multiplu de 10.')
        now=time.time() if now is None else now
        with self.db:
            if self.db.execute("SELECT 1 FROM casino_games WHERE channel=? AND phase IN ('lobby','playing')",(channel,)).fetchone():
                raise ValueError('Există deja o masă activă în acest canal.')
            recent=self.db.execute('SELECT MAX(created) FROM casino_games WHERE owner=?',(owner,)).fetchone()[0]
            if recent is not None and now<recent+60:
                raise ValueError('Așteaptă un minut între mesele pe care le creezi.')
            state=dict(players=[dict(id=owner,hand=[],stake=bet,status='waiting')],dealer=[],deck=[])
            cursor=self.db.execute("INSERT INTO casino_games(channel,owner,bet,phase,state,deadline,created) VALUES(?,?,?,'lobby',?,?,?)",
                                   (channel,owner,bet,json.dumps(state),now+60,now))
            number=cursor.lastrowid
            self._casino_reserve(owner,number,bet)
        return self.casino_game(number)

    def casino_message(self,number,message):
        with self.db:
            self.db.execute('UPDATE casino_games SET message=? WHERE id=?',(message,number))

    def _casino_live(self,number,phase=None):
        row=self.casino_game(number)
        if not row or row['phase'] not in ('lobby','playing') or (phase and row['phase']!=phase):
            raise ValueError('Masa nu mai este în faza potrivită. Folosește /blackjack pentru o masă nouă.')
        return row,json.loads(row['state'])

    def casino_join(self,number,member,now=None):
        now=time.time() if now is None else now
        with self.db:
            row,state=self._casino_live(number,'lobby')
            if now>=row['deadline'] or len(state['players'])>=5:
                raise ValueError('Înscrierile s-au încheiat sau masa este plină.')
            self._casino_reserve(member,number,row['bet'])
            state['players'].append(dict(id=member,hand=[],stake=row['bet'],status='waiting'))
            self._casino_save(number,state,'lobby',row['deadline'])
        return self.casino_game(number)

    def casino_leave(self,number,member,now=None):
        now=time.time() if now is None else now
        with self.db:
            row,state=self._casino_live(number,'lobby')
            if now>=row['deadline']:
                raise ValueError('Înscrierile s-au încheiat.')
            player=next((p for p in state['players'] if p['id']==member),None)
            if player is None:
                raise ValueError('Nu ești înscris la această masă.')
            if member==row['owner']:
                self._casino_void(row,state,'Gazda a anulat masa.')
            else:
                self.db.execute('UPDATE casino_accounts SET balance=balance+? WHERE member=?',(player['stake'],member))
                self.db.execute('DELETE FROM casino_seats WHERE member=?',(member,))
                state['players'].remove(player)
                self._casino_save(number,state,'lobby',row['deadline'])
        return self.casino_game(number)

    def casino_start(self,number,actor=None,now=None):
        now=time.time() if now is None else now
        with self.db:
            row,state=self._casino_live(number,'lobby')
            if actor is not None and actor!=row['owner']:
                raise ValueError('Numai gazda poate începe înainte de expirarea înscrierilor.')
            deck=list(range(52)); secrets.SystemRandom().shuffle(deck)
            state['deck']=deck
            for p in state['players']:
                p['hand']=[deck.pop(),deck.pop()]
                p['status']='stand' if natural(p['hand']) else 'playing'
            state['dealer']=[deck.pop(),deck.pop()]
            if natural(state['dealer']) or not any(p['status']=='playing' for p in state['players']):
                self._casino_settle(row,state)
            else:
                self._casino_save(number,state,'playing',now+45)
        return self.casino_game(number)

    def casino_move(self,number,member,move,revision,now=None,*,_expired=False):
        now=time.time() if now is None else now
        with self.db:
            row,state=self._casino_live(number,'playing')
            if row['revision']!=revision or (now>=row['deadline'] and not (_expired and move=='stand')):
                raise ValueError('Tura s-a schimbat sau a expirat. Verifică masa actualizată.')
            player=next((p for p in state['players'] if p['status']=='playing'),None)
            if not player or player['id']!=member:
                raise ValueError('Nu este rândul tău.')
            if move=='double':
                if len(player['hand'])!=2:
                    raise ValueError('Poți dubla numai la prima decizie a mâinii.')
                balance=self.db.execute('SELECT balance FROM casino_accounts WHERE member=?',(member,)).fetchone()[0]
                if balance<player['stake']:
                    raise ValueError('Nu ai suficiente jetoane pentru dublare.')
                self.db.execute('UPDATE casino_accounts SET balance=balance-? WHERE member=?',(player['stake'],member))
                player['stake']*=2
                self.db.execute('UPDATE casino_seats SET stake=? WHERE member=?',(player['stake'],member))
                player['hand'].append(state['deck'].pop()); player['status']='stand'
            elif move=='hit':
                player['hand'].append(state['deck'].pop())
                if total(player['hand'])>=21: player['status']='stand'
            elif move=='stand':
                player['status']='stand'
            else:
                raise ValueError('Acțiune invalidă.')
            if not any(p['status']=='playing' for p in state['players']):
                self._casino_settle(row,state)
            else:
                self._casino_save(number,state,'playing',now+45)
        return self.casino_game(number)

    def _casino_settle(self,row,state):
        while total(state['dealer'])<17:
            state['dealer'].append(state['deck'].pop())
        dealer=total(state['dealer'])
        for p in state['players']:
            score=total(p['hand']); stake=p['stake']
            if score>21:
                result,payout='loss',0
            elif natural(state['dealer']):
                result,payout=('push',stake) if natural(p['hand']) else ('loss',0)
            elif natural(p['hand']):
                result,payout='win',stake*5//2
            elif dealer>21 or score>dealer:
                result,payout='win',stake*2
            elif score==dealer:
                result,payout='push',stake
            else:
                result,payout='loss',0
            p.update(result=result,payout=payout,status='done')
            column={'win':'wins','loss':'losses','push':'pushes'}[result]
            self.db.execute(f'UPDATE casino_accounts SET balance=balance+?,{column}={column}+1 WHERE member=?',(payout,p['id']))
        self.db.execute('DELETE FROM casino_seats WHERE game=?',(row['id'],))
        state['deck']=[]
        self._casino_save(row['id'],state,'finished',0)

    def _casino_void(self,row,state,reason):
        seats=self.db.execute('SELECT * FROM casino_seats WHERE game=?',(row['id'],)).fetchall()
        for seat in seats:
            self.db.execute('UPDATE casino_accounts SET balance=balance+? WHERE member=?',(seat['stake'],seat['member']))
        self.db.execute('DELETE FROM casino_seats WHERE game=?',(row['id'],))
        state['cancel_reason']=reason; state['deck']=[]
        self._casino_save(row['id'],state,'cancelled',0)

    def casino_void(self,number,reason):
        with self.db:
            row=self.casino_game(number)
            if row and row['phase'] in ('lobby','playing'):
                self._casino_void(row,json.loads(row['state']),reason)
        return self.casino_game(number)

    def casino_recover(self):
        recovered=[]
        with self.db:
            for row in self.casino_active():
                self._casino_void(row,json.loads(row['state']),'Bot repornit; toate mizele au fost restituite.')
                recovered.append(self.casino_game(row['id']))
        return recovered

    def casino_tick(self,number,now=None):
        now=time.time() if now is None else now
        row=self.casino_game(number)
        if not row or row['phase'] not in ('lobby','playing') or now<row['deadline']:
            return None
        if row['phase']=='lobby':
            return self.casino_start(number,now=now)
        state=json.loads(row['state'])
        player=next(p for p in state['players'] if p['status']=='playing')
        # Expired turn stands automatically; caller holds the casino lock.
        return self.casino_move(number,player['id'],'stand',row['revision'],now=now,_expired=True)
