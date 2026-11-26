"""BLUFF Online — authoritative game server, one process, threaded WSGI."""
import json, os, random, secrets, threading, time
from collections import deque
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

RANKS = 'A 2 3 4 5 6 7 8 9 10 J Q K'.split()
SUITS = 'SHDC'
STYLES = ['Balanced', 'Conservative', 'Deceptive', 'Aggressive']
PERSONALITIES = [(.30,.25),(.20,.15),(.50,.30),(.35,.45)]
DECK = tuple(f'{r}-{s}-{d}' for d in range(3) for r in RANKS for s in SUITS)
DECK_SET = set(DECK)
HUMAN_PLAY = 35
HUMAN_REVIEW = 15
DISCONNECT = 25
LOBBY_WAIT = 8
MAX_ROOMS = 100
STATIC = Path(__file__).parent / 'static'

def rank(c): return c.split('-')[0]
def now(): return time.monotonic()

class Room:
    def __init__(self, t):
        self.id = secrets.token_hex(6)
        self.seats = [None]*4
        self.rev = 0; self.event_id = 0; self.events = deque(maxlen=30)
        self.last_human = t
        self.reset(t)

    def reset(self, t):
        deck = list(DECK); random.SystemRandom().shuffle(deck)
        self.hands = [deck[i::4] for i in range(4)]
        self.pile = []; self.turn = 0; self.ri = 0; self.number = 1
        self.claim = None; self.last = []; self.revealed = []; self.reviewer = None
        self.queue = []; self.result = None; self.winner = None
        self.events.clear()
        self.set_phase('lobby', t, LOBBY_WAIT)
        self.emit('deal', 'Three decks shuffled. Looking for players…')

    def name(self, i):
        return self.seats[i]['name'] if self.seats[i] else f'Bot {i+1}'

    def set_phase(self, phase, t, duration):
        self.phase = phase; self.deadline = t + duration; self.duration = duration
        self.rev += 1

    def emit(self, kind, message, **data):
        self.event_id += 1
        self.events.append(dict(id=self.event_id, kind=kind, text=message, **data))
        self.rev += 1

    def joinable(self):
        # Matchmaking happens before the deal starts. Never inherit a stranger's hand.
        return self.phase == 'lobby' and any(s is None for s in self.seats)

    def add(self, token, t):
        i = self.seats.index(None)
        self.seats[i] = dict(token=token, name=f'Player {i+1}', seen=t)
        self.last_human = t
        self.emit('join', f'{self.name(i)} joined the table.')
        if all(self.seats): self.start(t)
        return i

    def start(self,t):
        self.emit('start', f'Game started with {sum(s is not None for s in self.seats)} human player(s).')
        self.begin_turn(t)

    def begin_turn(self,t):
        self.set_phase('play',t,HUMAN_PLAY if self.seats[self.turn] else 1.35)
        self.emit('turn',f'{self.name(self.turn)}: play {RANKS[self.ri]}.',seat=self.turn)

    def bot_cards(self):
        hand=self.hands[self.turn]; r=RANKS[self.ri]
        good=[c for c in hand if rank(c)==r]; bad=[c for c in hand if rank(c)!=r]
        if not good: return random.sample(hand,random.randint(1,min(5,len(hand))))
        extra=min(3,12-len(good),len(bad))
        if extra and random.random()<PERSONALITIES[self.turn][0]:
            good+=random.sample(bad,random.randint(1,extra))
        return good

    def bot_calls(self,i):
        own=sum(rank(c)==RANKS[self.ri] for c in self.hands[i]); count=len(self.last)
        if own+count>12: return True
        left=len(self.hands[self.turn]); p=PERSONALITIES[i][1]+(count-1)*.05+own*.06
        p+=.5 if left==0 else .25 if left<=3 else 0
        p-=min(len(self.pile)*.003,.25)
        return random.random()<max(.03,min(.95,p))

    def play(self,cards,t):
        if self.phase!='play' or not isinstance(cards,list) or not 1<=len(cards)<=12 or not all(isinstance(c,str) for c in cards) or len(set(cards))!=len(cards) or not all(c in self.hands[self.turn] for c in cards):
            raise ValueError('Select 1–12 different cards from your hand.')
        for c in cards: self.hands[self.turn].remove(c)
        self.pile.extend(cards); self.last=list(cards); self.revealed=[]; self.result=None
        self.claim=dict(seat=self.turn,name=self.name(self.turn),count=len(cards),rank=RANKS[self.ri])
        self.emit('play',f'{self.name(self.turn)} claims {len(cards)} × {RANKS[self.ri]}.',seat=self.turn,count=len(cards))
        self.queue=[(self.turn+i)%4 for i in range(1,4)]
        self.reviewer=None; self.set_phase('between',t,.65); self.check()

    def next_review(self,t):
        if not self.queue:
            self.emit('pass','Nobody challenged.'); self.reviewer=None
            self.set_phase('finish',t,.65); return
        self.reviewer=self.queue.pop(0)
        self.set_phase('review',t,HUMAN_REVIEW if self.seats[self.reviewer] else 1.05)
        self.emit('review',f'{self.name(self.reviewer)} is deciding.',seat=self.reviewer)

    def review(self,call,t):
        i=self.reviewer
        if call:
            self.emit('challenge',f'{self.name(i)} calls BLUFF!',seat=i)
            self.set_phase('reveal',t,.65)
        else:
            self.emit('pass',f'{self.name(i)} passes.',seat=i)
            self.reviewer=None; self.set_phase('between',t,.35)

    def reveal(self,t):
        lie=any(rank(c)!=RANKS[self.ri] for c in self.last)
        loser=self.turn if lie else self.reviewer
        self.revealed=list(self.last)
        self.result=dict(lie=lie,loser=loser,count=len(self.pile),text='BLUFF CAUGHT!' if lie else 'TRUTHFUL CLAIM!')
        self.emit('caught' if lie else 'truth',f"{self.result['text']} {self.name(loser)} takes {len(self.pile)} cards.")
        self.set_phase('collect',t,3.0)

    def collect(self,t):
        i=self.result['loser']; count=len(self.pile)
        self.hands[i].extend(self.pile); self.pile.clear()
        self.emit('collect',f'{self.name(i)} collected {count} cards.',seat=i,count=count)
        self.set_phase('finish',t,.8); self.check()

    def finish(self,t):
        if not self.hands[self.turn]:
            self.winner=self.turn; self.set_phase('won',t,0)
            self.emit('win',f'{self.name(self.winner)} wins!',seat=self.winner); return
        self.turn=(self.turn+1)%4; self.ri=(self.ri+1)%13; self.number+=1
        self.claim=None; self.revealed=[]; self.result=None; self.reviewer=None; self.last=[]
        self.begin_turn(t)

    def check(self):
        cards=[c for h in self.hands for c in h]+self.pile
        assert len(cards)==156 and set(cards)==DECK_SET

    def drop(self,i,t):
        name=self.name(i); self.seats[i]=None
        self.emit('leave',f'{name} left. A bot takes over the seat.')
        if (self.phase=='play' and self.turn==i) or (self.phase=='review' and self.reviewer==i):
            self.deadline=min(self.deadline,t+1); self.duration=max(.1,self.deadline-t)
        if self.phase=='lobby' and not any(self.seats): self.deadline=t+LOBBY_WAIT

    def tick(self,t):
        for i,s in enumerate(self.seats):
            if s and t-s['seen']>DISCONNECT: self.drop(i,t)
        if any(self.seats): self.last_human=t
        else: return
        if t<self.deadline: return
        if self.phase=='lobby': self.start(t)
        elif self.phase=='play':
            if self.seats[self.turn]: self.emit('timeout',f'{self.name(self.turn)} timed out. Bot plays this turn.')
            self.play(self.bot_cards(),t)
        elif self.phase=='between': self.next_review(t)
        elif self.phase=='review':
            if self.seats[self.reviewer]: self.emit('timeout',f'{self.name(self.reviewer)} timed out. Automatic pass.')
            self.review(False if self.seats[self.reviewer] else self.bot_calls(self.reviewer),t)
        elif self.phase=='reveal': self.reveal(t)
        elif self.phase=='collect': self.collect(t)
        elif self.phase=='finish': self.finish(t)

    def snapshot(self,i,t):
        return dict(room=self.id,seat=i,rev=self.rev,phase=self.phase,turn=self.turn,reviewer=self.reviewer,
            rank=RANKS[self.ri],next_rank=RANKS[(self.ri+1)%13],number=self.number,pile=len(self.pile),
            players=[dict(name=self.name(j),human=bool(self.seats[j]),style='Human' if self.seats[j] else STYLES[j],count=len(self.hands[j])) for j in range(4)],
            hand=sorted(self.hands[i],key=lambda c:(RANKS.index(rank(c)),SUITS.index(c.split('-')[1]),c)),
            claim=self.claim,revealed=self.revealed,result=self.result,winner=self.winner,
            remaining=max(0,self.deadline-t),duration=self.duration,events=list(self.events),
            can_play=self.phase=='play' and self.turn==i,can_review=self.phase=='review' and self.reviewer==i)

class Matchmaker:
    def __init__(self):
        self.rooms={}; self.lock=threading.RLock()
    def locate(self,token):
        if not token: return None,None
        for room in self.rooms.values():
            for i,s in enumerate(room.seats):
                if s and secrets.compare_digest(s['token'],token): return room,i
        return None,None
    def join(self,token,t):
        room,i=self.locate(token)
        if room: room.seats[i]['seen']=t; return token,room,i
        room=next((r for r in self.rooms.values() if r.joinable()),None)
        if room is None:
            if len(self.rooms)>=MAX_ROOMS: raise ValueError('Server is full. Try again shortly.')
            room=Room(t); self.rooms[room.id]=room
        token=secrets.token_urlsafe(32); i=room.add(token,t)
        return token,room,i
    def tick(self,t):
        with self.lock:
            for key,r in list(self.rooms.items()):
                r.tick(t)
                if not any(r.seats) and t-r.last_human>30: del self.rooms[key]

MATCH=Matchmaker()
def ticker():
    while True:
        try: MATCH.tick(now())
        except Exception:
            import traceback; traceback.print_exc()
        time.sleep(.15)
threading.Thread(target=ticker,daemon=True).start()

STATIC_FILES={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','application/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}

def application(env,start):
    path=env.get('PATH_INFO','/'); method=env['REQUEST_METHOD']; token=env.get('HTTP_X_PLAYER_TOKEN','')
    def respond(code,data,ctype='application/json; charset=utf-8'):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode()
        start(code,[('Content-Type',ctype),('Content-Length',str(len(data))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),('X-Frame-Options','DENY'),('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")]); return [data]
    if method=='GET' and path in STATIC_FILES:
        name,ctype=STATIC_FILES[path]; return respond('200 OK',(STATIC/name).read_bytes(),ctype)
    if method=='GET' and path=='/health': return respond('200 OK',{'ok':True})
    if path not in ('/api/join','/api/state','/api/action','/api/leave'): return respond('404 Not Found',{'error':'Not found'})
    if (path=='/api/state' and method!='GET') or (path!='/api/state' and method!='POST'): return respond('405 Method Not Allowed',{'error':'Invalid method'})
    try:
        n=int(env.get('CONTENT_LENGTH') or '0')
        if not 0<=n<=8192: return respond('413 Payload Too Large',{'error':'Request too large'})
        body=json.loads(env['wsgi.input'].read(n)) if n else {}
        if not isinstance(body,dict): raise ValueError('Invalid request')
        with MATCH.lock:
            t=now()
            if path=='/api/join':
                token,room,i=MATCH.join(token,t)
                return respond('200 OK',{'token':token,'state':room.snapshot(i,t)})
            room,i=MATCH.locate(token)
            if room is None: return respond('410 Gone',{'error':'Seat expired. Join a new game.'})
            room.seats[i]['seen']=t
            if path=='/api/leave': room.drop(i,t); return respond('200 OK',{'ok':True})
            if path=='/api/action':
                action=body.get('action')
                if action=='rematch' and room.phase=='won': room.reset(t)
                elif body.get('rev')!=room.rev: return respond('409 Conflict',{'error':'Turn changed. Try again.','state':room.snapshot(i,t)})
                elif action=='play' and room.phase=='play' and room.turn==i and t<room.deadline: room.play(body.get('cards'),t)
                elif action in ('bluff','pass') and room.phase=='review' and room.reviewer==i and t<room.deadline: room.review(action=='bluff',t)
                else: return respond('409 Conflict',{'error':'That action is not available now.','state':room.snapshot(i,t)})
            return respond('200 OK',{'state':room.snapshot(i,t)})
    except (ValueError,TypeError,UnicodeError) as e: return respond('400 Bad Request',{'error':str(e)})

class ThreadedServer(ThreadingMixIn,WSGIServer):
    daemon_threads=True
if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'BLUFF Online: http://localhost:{port}',flush=True)
    with make_server('0.0.0.0',port,application,server_class=ThreadedServer) as server: server.serve_forever()
