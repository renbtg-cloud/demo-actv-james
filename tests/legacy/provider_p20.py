#!/usr/bin/env python3
"""P20 experimental independently durable provider with Ed25519 signed lookup snapshots.
Authenticity only: a dishonest or incorrect issuer may sign a false report. NOT proof of truth.
"""
import os, sqlite3, time, urllib.parse, sys, base64
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from cryptography.hazmat.primitives.serialization import load_pem_private_key
DB=os.environ['P19_PROVIDER_SQLITE']; PORT=int(os.environ.get('P19_PROVIDER_PORT','18782'))
TTL=int(os.environ.get('P19_PROVIDER_RETENTION_MS','900'))
KEY_PATH=os.environ['P20_PRIVATE_KEY_PEM']; ISSUER=os.environ.get('P20_ISSUER','deroyque-lab-provider')
KID=os.environ.get('P20_KID','lab-key-01'); OVERRIDE=os.environ.get('P20_LOOKUP_OVERRIDE','NONE')
with open(KEY_PATH,'rb') as f: SIGNER=load_pem_private_key(f.read(), password=None)
assert KEY_PATH and ISSUER and KID

def b64(data):return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None)
 c.execute('PRAGMA busy_timeout=10000');c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA journal_mode=WAL');return c
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
  gen=(row[0]+1 if row else 1) if fresh else row[0]
  if fresh:
   c.execute('INSERT INTO idem_index(key,generation,expires_ms) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET generation=excluded.generation,expires_ms=excluded.expires_ms',(key,gen,now+TTL))
   c.execute('INSERT INTO effect(key,generation,outcome,created_ms) VALUES(?,?,?,?)',(key,gen,'REJECTED' if mode=='reject' else 'ACTIVATED',now))
  outcome=c.execute('SELECT outcome FROM effect WHERE key=? AND generation=?',(key,gen)).fetchone()[0]
  c.execute('COMMIT');return fresh,gen,outcome
 except BaseException:
  c.execute('ROLLBACK');raise
 finally:c.close()

def attestation(key,nonce,outcome,count,gen):
 k=key; n=nonce;kid=KID; ts=time.time_ns()//1_000_000
 if OVERRIDE=='WRONG_KEY':k='other-'+k
 if OVERRIDE=='WRONG_NONCE':n='other-'+n
 if OVERRIDE=='WRONG_KID':kid='unknown-key'
 if OVERRIDE=='STALE':ts-=120000
 payload=('P20V1\n'+ISSUER+'\n'+kid+'\n'+b64(k.encode())+'\n'+outcome+'\n'+str(count)+'\n'+str(gen)+'\n'+str(ts)+'\n'+n).encode('ascii')
 sig=SIGNER.sign(payload)
 if OVERRIDE=='BAD_SIGNATURE':sig=bytes([sig[0]^0xFF])+sig[1:]
 return b64(payload)+'.'+b64(sig)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):print('HTTP',fmt%args,flush=True)
 def send(self,status,body=b'',receipt=None,proof=None):
  self.send_response(status)
  if receipt:self.send_header('X-Provider-Receipt',receipt)
  if proof:self.send_header('X-Provider-Attestation',proof)
  self.send_header('Content-Length',str(len(body)))
  self.end_headers()
  try:self.wfile.write(body)
  except (BrokenPipeError,ConnectionResetError):pass
 def do_GET(self):
  u=urllib.parse.urlsplit(self.path)
  if u.path=='/health':return self.send(200,b'UP')
  if u.path!='/lookup':return self.send(404)
  q=urllib.parse.parse_qs(u.query);key=q.get('key',[''])[0];nonce=q.get('nonce',[''])[0]
  if not key or len(key)>100 or not nonce or len(nonce)>80:return self.send(400,b'BAD_CHALLENGE')
  with connect() as c:rows=c.execute('SELECT generation,outcome FROM effect WHERE key=? ORDER BY generation',(key,)).fetchall()
  count=len(rows);gen=rows[-1][0] if rows else 0
  outcome='ABSENT' if not count else rows[0][1] if count==1 else 'MULTIPLE'
  if OVERRIDE=='ABSENT':outcome='ABSENT';count=0;gen=0
  if OVERRIDE=='REJECTED':outcome='REJECTED';count=1;gen=1
  if OVERRIDE=='ACTIVATED':outcome='ACTIVATED';count=1;gen=1
  if OVERRIDE=='MULTIPLE':outcome='MULTIPLE';count=max(count,2);gen=max(gen,2)
  status={'ABSENT':404,'MULTIPLE':409,'REJECTED':422,'ACTIVATED':200}[outcome]
  proof=None if OVERRIDE=='UNSIGNED' else attestation(key,nonce,outcome,count,gen)
  self.send(status,('SNAPSHOT_'+outcome).encode(),proof=proof)
 def do_POST(self):
  if self.path!='/activate':return self.send(404)
  data=self.rfile.read(min(int(self.headers.get('Content-Length','0')),2048))
  q=urllib.parse.parse_qs(data.decode('utf-8'));key=q.get('key',[''])[0];mode=q.get('mode',[''])[0]
  if not key or len(key)>100:return self.send(400)
  if mode=='before500':return self.send(503,b'FAIL_BEFORE_EFFECT')
  fresh,gen,outcome=effect(key,mode)
  print('DURABLE_EFFECT',key,'generation='+str(gen),'fresh='+str(fresh),'mode='+mode,flush=True)
  if mode=='crash' and fresh:sys.stdout.flush();os._exit(78)
  if mode=='drop' and fresh:
   self.close_connection=True
   try:self.connection.shutdown(2)
   except OSError:pass
   self.connection.close();return
  if mode in ('after500','after503','always503') and (fresh or mode=='always503'):
   return self.send(503,b'COMMITTED_BUT_UNCERTAIN')
  # deliberately unsigned HTTP activation receipts: signed-mode client MUST NOT trust these
  return self.send(422 if outcome=='REJECTED' else 200,b'OK',f'MOCK:{outcome}:{key}')
if __name__=='__main__':
 init();server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
 print('P20_PROVIDER_READY sqlite='+DB+' port='+str(PORT)+' override='+OVERRIDE,flush=True)
 server.serve_forever(poll_interval=.05)
