#!/usr/bin/env python3
"""P19 independent mock provider. SQLite effects ledger, WAL, FULL sync.
Test-only HTTP contract: lookups are trusted in the lab; NOT cryptographic evidence.
"""
import os, sqlite3, time, urllib.parse, sys, json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

DB=os.environ.get('P19_PROVIDER_SQLITE','/mnt/data/P19/provider.sqlite3')
PORT=int(os.environ.get('P19_PROVIDER_PORT','18782'))
TTL=int(os.environ.get('P19_PROVIDER_RETENTION_MS','1600'))
STALE=os.environ.get('P19_LOOKUP_FORCE_MISS','0')=='1'
FORGE=os.environ.get('P19_LOOKUP_FORGE_RECEIPT','0')=='1'

def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.execute('PRAGMA busy_timeout=10000')
    c.execute('PRAGMA synchronous=FULL')
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init():
    os.makedirs(os.path.dirname(os.path.abspath(DB)),exist_ok=True)
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS idem_index (key TEXT PRIMARY KEY, generation INTEGER NOT NULL, expires_ms INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS effect (key TEXT NOT NULL, generation INTEGER NOT NULL, outcome TEXT NOT NULL, created_ms INTEGER NOT NULL, PRIMARY KEY(key,generation))')

def effect(key,mode):
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        now=time.time_ns()//1_000_000
        row=c.execute('SELECT generation,expires_ms FROM idem_index WHERE key=?',(key,)).fetchone()
        fresh=row is None or row[1]<=now
        generation=(row[0]+1 if row else 1) if fresh else row[0]
        if fresh:
            c.execute('INSERT INTO idem_index(key,generation,expires_ms) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET generation=excluded.generation,expires_ms=excluded.expires_ms',(key,generation,now+TTL))
            c.execute('INSERT INTO effect(key,generation,outcome,created_ms) VALUES(?,?,?,?)',(key,generation,'REJECTED' if mode=='reject' else 'ACTIVATED',now))
        outcome=c.execute('SELECT outcome FROM effect WHERE key=? AND generation=?',(key,generation)).fetchone()[0]
        c.execute('COMMIT')
        return fresh,generation,outcome
    except BaseException:
        c.execute('ROLLBACK')
        raise
    finally:c.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):print('HTTP',fmt%args,flush=True)
    def send(self,status,body=b'',receipt=None):
        self.send_response(status)
        if receipt:self.send_header('X-Provider-Receipt',receipt)
        self.send_header('Content-Type','text/plain; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):pass
    def do_GET(self):
        u=urllib.parse.urlsplit(self.path)
        if u.path=='/health':return self.send(200,b'UP')
        if u.path!='/lookup':return self.send(404)
        key=urllib.parse.parse_qs(u.query).get('key',[''])[0]
        if not key or len(key)>100:return self.send(400)
        if STALE:return self.send(404,b'LOOKUP_MISS_NOT_PROOF')
        with connect() as c:rows=c.execute('SELECT outcome FROM effect WHERE key=? ORDER BY generation',(key,)).fetchall()
        if not rows:return self.send(404,b'NOT_FOUND_NOT_PROOF')
        if len(rows)!=1:return self.send(409,b'MULTIPLE_EFFECTS_MANUAL_RECONCILIATION')
        self.send(200,b'FOUND_ONE_EFFECT',f'MOCK:{rows[0][0]}:{"OTHER_KEY" if FORGE else key}')
    def do_POST(self):
        if self.path!='/activate':return self.send(404)
        data=self.rfile.read(min(int(self.headers.get('Content-Length','0')),2048))
        form=urllib.parse.parse_qs(data.decode('utf-8'))
        key=form.get('key',[''])[0];mode=form.get('mode',[''])[0]
        if not key or len(key)>100:return self.send(400)
        if mode=='before500':return self.send(503,b'FAIL_BEFORE_EFFECT')
        fresh,gen,outcome=effect(key,mode)
        print('DURABLE_EFFECT',key,'generation='+str(gen),'fresh='+str(fresh),'mode='+mode,flush=True)
        if mode=='crash' and fresh:
            sys.stdout.flush();os._exit(78)
        if mode=='drop' and fresh:
            self.close_connection=True
            try:self.connection.shutdown(2)
            except OSError:pass
            self.connection.close();return
        if mode in ('after500','after503','always503') and (fresh or mode=='always503'):
            return self.send(503,b'COMMITTED_BUT_UNCERTAIN')
        if mode=='malformed':return self.send(200,b'BAD_RECEIPT','MOCK:ACTIVATED:WRONG_KEY')
        return self.send(422 if outcome=='REJECTED' else 200,b'OK',f'MOCK:{outcome}:{key}')

if __name__=='__main__':
    init()
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    print('P19_PROVIDER_READY sqlite='+DB+' port='+str(PORT)+' ttl='+str(TTL),flush=True)
    server.serve_forever(poll_interval=.05)
