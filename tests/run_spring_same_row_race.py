#!/usr/bin/env python3
"""Real Spring Boot SAME-ROW contention: four distinct consumer JVMs visibly enter
one request while PostgreSQL holds its row lock; duplicate quorum-queue hints,
then release the lock and audit exactly one authorization and external effect.
No mock JDBC implementation, JVM threads as peers, or relaxed success criteria.
"""
import argparse, hashlib, json, os, pathlib, re, secrets, signal, socket, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid

ROOT=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--boot-jar',required=True,type=pathlib.Path)
parser.add_argument('--output',type=pathlib.Path)
args=parser.parse_args()
PG={k:os.environ.get('DEROYQUE_PG_'+k.upper(),v) for k,v in {
 'host':'127.0.0.1','port':'5432','db':'deroyque_ci','user':'deroyque_ci','password':''}.items()}
READ,WRITE=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
TAG=uuid.uuid4().hex[:12];SCHEMA='m17_'+TAG;QUEUE='m17.'+TAG;KEY='m17-'+TAG+'-same-row';RID=str(uuid.uuid4())
RESULT={'scenario':'same-row-spring-contention','checks':[],'participants':{},'completed':False}
TMP=tempfile.TemporaryDirectory(prefix='deroyque-same-row-'); DIR=pathlib.Path(TMP.name)
CHILDREN=[];LOCKER=None

def check(ok,name):
 ok=bool(ok);RESULT['checks'].append({'passed':ok,'name':name});n=len(RESULT['checks'])
 print(f'{"PASS" if ok else "FAIL"} {n}/{n} {name}',flush=True)
 if not ok:raise AssertionError(name)

def sql(statement):
 p=subprocess.run([os.environ.get('DEROYQUE_PSQL','psql'),'-X','-A','-t','-v','ON_ERROR_STOP=1',
   '-h',PG['host'],'-p',PG['port'],'-U',PG['user'],'-d',PG['db'],'-c',statement],
   env={**os.environ,'PGPASSWORD':PG['password']},capture_output=True,text=True,timeout=15)
 if p.returncode:raise RuntimeError('PostgreSQL failure during '+statement.split()[0])
 return p.stdout.strip()

def free_port():
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def launch(name,cmd,env):
 f=(DIR/(name+'.log')).open('w')
 p=subprocess.Popen(cmd,env={**os.environ,**env},stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
 CHILDREN.append((name,p,f));return p

def stop(p):
 if p.poll() is None:
  try:os.killpg(p.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  try:p.wait(timeout=4)
  except subprocess.TimeoutExpired:
   try:os.killpg(p.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   p.wait(timeout=5)

def eventually(fn,timeout=30):
 due=time.monotonic()+timeout
 while time.monotonic()<due:
  try:
   res=fn()
   if res:return res
  except (OSError,ValueError,urllib.error.URLError,sqlite3.OperationalError):pass
  time.sleep(.15)
 return None

def http(port,path,token=None):
 headers={'Authorization':'Bearer '+token} if token else {}
 try:
  with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers),timeout=2) as r:
   raw=r.read().decode()
   try:payload=json.loads(raw)
   except ValueError:payload={}
   return r.status,payload
 except urllib.error.HTTPError as e:return e.code,{}

def body_stats():
 stats=[]
 for name,p,_ in CHILDREN:
  if not name.startswith('spring-'):continue
  log=(DIR/(name+'.log')).read_text(errors='replace')
  enter=len(re.findall(r'^P21_RACE_ENTER rid='+re.escape(RID)+r'\b',log,re.M))
  outcomes=re.findall(r'^P21_RACE_ACQUIRE rid='+re.escape(RID)+r'\b[^\n]*result=(\w+)',log,re.M)
  stats.append({'role':name,'pid':p.pid,'alive':p.poll() is None,'entered':enter,
                'committed':outcomes.count('COMMITTED'),'stale':outcomes.count('STALE'),
                'outcomes':len(outcomes)})
 return stats

def row():
 raw=sql(f"SELECT stage,attempts,(SELECT count(*) FROM {SCHEMA}.p9_dispatch WHERE rid='{RID}'),"+
         f"(SELECT count(*) FROM {SCHEMA}.p9_settlement WHERE rid='{RID}'),"+
         f"(SELECT count(*) FROM {SCHEMA}.p20_signed_attestation WHERE did IN " +
         f"(SELECT did FROM {SCHEMA}.p9_dispatch WHERE rid='{RID}')) " +
         f"FROM {SCHEMA}.p9_requests WHERE rid='{RID}'")
 if not raw:return None
 a=raw.split('|');return {'state':a[0],'attempts':int(a[1]),'dispatches':int(a[2]),'settlements':int(a[3]),'signed':int(a[4])}

def effects():
 with sqlite3.connect(DIR/'provider.sqlite3',timeout=5) as c:
  return c.execute('SELECT count(*) FROM effect WHERE key=?',(KEY,)).fetchone()[0]

def create_locker():
 global LOCKER
 env={**os.environ,'PGPASSWORD':PG['password']}
 LOCKER=subprocess.Popen([os.environ.get('DEROYQUE_PSQL','psql'),'-X','-q','-t','-A','-v','ON_ERROR_STOP=1',
    '-h',PG['host'],'-p',PG['port'],'-U',PG['user'],'-d',PG['db']],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,env=env)
 LOCKER.stdin.write('BEGIN;\n')
 LOCKER.stdin.write(f"SELECT rid FROM {SCHEMA}.p9_requests WHERE rid='{RID}' FOR UPDATE;\n")
 LOCKER.stdin.write('\\echo ROW_LOCK_HELD\n');LOCKER.stdin.flush()
 # Uses select for the row; psql echoes it plus our sentinel, without disclosure of credentials.
 import selectors
 sel=selectors.DefaultSelector();sel.register(LOCKER.stdout,selectors.EVENT_READ)
 until=time.monotonic()+10;found=False;captured=b'' 
 while time.monotonic()<until:
  if not sel.select(timeout=.25):continue
  fragment=os.read(LOCKER.stdout.fileno(),4096)
  if not fragment:break
  captured+=fragment
  if b'ROW_LOCK_HELD' in captured:found=True;break
 sel.close()
 check(found and LOCKER.poll() is None,'PostgreSQL transaction holds the exact request row lock before Rabbit hints')

def unlock():
 global LOCKER
 if LOCKER is None:return
 if LOCKER.poll() is None:
  try:
   LOCKER.stdin.write('COMMIT;\n\\q\n');LOCKER.stdin.flush();LOCKER.communicate(timeout=8)
  except (BrokenPipeError,subprocess.TimeoutExpired):
   LOCKER.kill();LOCKER.wait()
 LOCKER=None

def publish_hints(count):
 import pika
 conn=pika.BlockingConnection(pika.ConnectionParameters(host=os.getenv('P13_RABBIT_HOST','127.0.0.1'),
   port=int(os.getenv('P13_RABBIT_PORT','5672')),credentials=pika.PlainCredentials(
   os.getenv('P13_RABBIT_USER','guest'),os.getenv('P13_RABBIT_PASSWORD','guest')),heartbeat=20))
 try:
  ch=conn.channel();ch.confirm_delivery()
  for i in range(count):
   ch.basic_publish(exchange='',routing_key=QUEUE,body=RID.encode(),mandatory=True,
     properties=pika.BasicProperties(delivery_mode=2,message_id=f'claim-race:{RID}:{i}',content_type='text/plain'))
 finally:conn.close()

def run():
 check(args.boot_jar.is_file(),'actual Spring Boot executable JAR exists')
 sql('CREATE SCHEMA '+SCHEMA)
 pp=free_port()
 provider_env={'P19_PROVIDER_PORT':str(pp),'P19_PROVIDER_SQLITE':str(DIR/'provider.sqlite3'),
  'P19_PROVIDER_RETENTION_MS':'600000','P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
  'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01'}
 p=launch('provider',['python3','-u',str(ROOT/'provider/independent_provider.py')],provider_env)
 check(bool(eventually(lambda:p.poll() is None and http(pp,'/health')[0]==200,15)),'independent durable provider ready')
 ports=[]
 for i in range(4):
  port=free_port();ports.append(port)
  jdbc=f'jdbc:postgresql://{PG["host"]}:{PG["port"]}/{PG["db"]}?currentSchema={SCHEMA}'
  env={'P13_JDBC':jdbc,'P13_DB_USER':PG['user'],'P13_DB_PASSWORD':PG['password'],
   'SPRING_DATASOURCE_URL':jdbc,'SPRING_DATASOURCE_USERNAME':PG['user'],'SPRING_DATASOURCE_PASSWORD':PG['password'],
   'SPRING_FLYWAY_SCHEMAS':SCHEMA,'SPRING_FLYWAY_DEFAULT_SCHEMA':SCHEMA,
   'P13_QUEUE':QUEUE,'P13_API_PORT':str(port),'SERVER_PORT':str(port),'SERVER_ADDRESS':'127.0.0.1',
   'P13_PROVIDER_URL':f'http://127.0.0.1:{pp}/activate','P19_RECONCILE_URL':f'http://127.0.0.1:{pp}/lookup',
   'P16_PROVIDER_RETENTION_MS':'300000','P20_SIGNED_LOOKUP_REQUIRED':'1',
   'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
   'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
   'APP_READ_TOKEN':READ,'APP_WRITE_TOKEN':WRITE,'APP_AUTH_MODE':'lab','APP_API_ENABLED':'1','APP_LAB_METRICS_ENABLED':'0',
   'P21_TRACE_CLAIM_RACE':'1'}
  spring=launch(f'spring-{i}',['java','-Xmx320m','-jar',str(args.boot_jar)],env)
  check(bool(eventually(lambda port=port,spring=spring:spring.poll() is None and
    http(port,'/api/v1/health/readiness')[0]==200,60)),f'Spring Boot worker JVM {i+1}/4 ready')
 check(len({p.pid for n,p,_ in CHILDREN if n.startswith('spring-')})==4,'four independent service JVM OS process IDs')
 check(sql(f'SELECT count(*) FROM {SCHEMA}.flyway_schema_history WHERE success')=='2','real Flyway V1/V2 applied')
 revision=sql(f'SELECT revision FROM {SCHEMA}.p16_policy LIMIT 1')
 check(bool(revision),'pinned policy registered by real Spring engine')
 fingerprint='public:'+hashlib.sha256(b'customer-race\x00product-race').hexdigest()
 now=int(sql('SELECT (EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT'))
 # Intentional INTERNAL seed, NOT claimed to be a public HTTP admission.
 # No outbox row is inserted: only explicit persistent Rabbit hints drive this race.
 sql(f"INSERT INTO {SCHEMA}.p9_requests "
   '(rid,idem_key,fingerprint,stage,ver,attempts,due_ms,lease_ms,audit_ms,policy_revision,retention_ms,customer_id,product_code) '
   f"VALUES ('{RID}','{KEY}','{fingerprint}','READY',0,0,{now-500},0,{now+30000},'{revision}',300000,'customer-race','product-race')")
 check(row()['state']=='READY' and row()['dispatches']==0,'same target row seeded READY without automatic outbox delivery')
 create_locker()
 publish_hints(96)
 check(bool(eventually(lambda: len([x for x in body_stats() if x['entered']>0])==4,18)),
   'four distinct Spring consumer JVMs entered the SAME row while its DB lock was held')
 check(row()['state']=='READY' and row()['dispatches']==0,'no worker advanced state during held row lock')
 before=body_stats();RESULT['participants_before_release']=before
 # Forbid a fake overlap: the first four JVMs really entered before the row lock was released.
 unlock()
 check(bool(eventually(lambda:(row() or {}).get('state')=='DONE',65)),
   'after lock release, fenced claim and signed provider evidence settle request DONE')
 check(bool(eventually(lambda:sum(x['committed'] for x in body_stats())>=1,10)),
   'at least one real Spring JVM recorded committed DB claim')
 time.sleep(1)
 after=body_stats(); RESULT['participants']=after
 check(len([x for x in after if x['entered']>0])==4,
   'all four independent Spring JVMs genuinely participated in contention')
 check(sum(x['committed'] for x in after)==1,
   'exactly ONE worker committed a claim for the shared request')
 check(sum(x['stale'] for x in after)>=2,
   'at least two real worker claim attempts lost the optimistic/row-locked race')
 final=row();RESULT['final_row']=final
 check(final['attempts']==1 and final['dispatches']==1 and final['settlements']==1 and final['signed']==1,
   'one authorization, one dispatch, one settlement and one durable signed proof')
 check(effects()==1,'independent SQLite provider recorded exactly one business effect')
 check(all(p.poll() is None for name,p,_ in CHILDREN if name.startswith('spring-')),
   'all four Spring Boot JVMs survived the race')
 code,data=http(ports[0],'/api/v1/requests/'+RID,READ)
 check(code==200 and data.get('id')==RID and data.get('status')=='DONE',
   'public status API reads the same final durable UUID')
 RESULT.update({'completed':True,'hint_messages':96,'service_jvms':4,'business_effects':effects()})
 print('SAME_ROW_RACE_PASS Spring_JVMs=4 claim_winners=1 '+
       f'claim_losers={sum(x["stale"] for x in after)} hints=96 effects=1',flush=True)

def main():
 code=1
 try:run();code=0
 except Exception as e:RESULT['error_type']=type(e).__name__;print('SAME_ROW_RACE_ERROR '+type(e).__name__,flush=True)
 finally:
  unlock()
  for name,p,f in reversed(CHILDREN):stop(p);f.close()
  bad=sum(not c['passed'] for c in RESULT['checks']);total=len(RESULT['checks'])
  print(f'SAME_ROW_RACE_RESULT FAILURES {bad}/{total} PASSED {total-bad}/{total}',flush=True)
  RESULT.update({'failed':bad,'passed':total-bad,'total':total,'exit_code':code})
  if args.output:
   args.output.parent.mkdir(parents=True,exist_ok=True)
   args.output.write_text(json.dumps(RESULT,indent=2)+'\n')
  TMP.cleanup()
 return code

if __name__=='__main__':sys.exit(main())
