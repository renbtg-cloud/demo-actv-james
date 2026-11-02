#!/usr/bin/env python3
"""Integrated application tests against *actual* PostgreSQL, RabbitMQ, JVM and independent SQLite provider.
   Disposable schema/queue; no shared infra stop/drop. Exit nonzero on any failure.
"""
import concurrent.futures, json, os, pathlib, signal, socket, sqlite3, subprocess, time, urllib.error, urllib.request, uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]
PG=os.environ.get('DEROYQUE_PSQL','/mnt/data/infra-lab/pgsql/usr/lib/postgresql/17/bin/psql')
PGJAR=os.environ.get('POSTGRES_JAR','/mnt/data/postgresql-42.7.14.jar')
RABBITJAR=os.environ.get('RABBITMQ_CLIENT_JAR','/mnt/data/amqp-client-5.37.0.jar')
CP=os.environ.get('DEROYQUE_TEST_CP',f'{ROOT}/build/classes:{PGJAR}:{RABBITJAR}')
TOKEN_READ='test-read-'+uuid.uuid4().hex
TOKEN_WRITE='test-write-'+uuid.uuid4().hex
TAG=uuid.uuid4().hex[:10]
SCHEMA='deroyque_app_'+TAG
QUEUE='deroyque.app.'+TAG
RUNTIME=ROOT/'build'/'runtime';RUNTIME.mkdir(parents=True,exist_ok=True)
SQLITE=RUNTIME/('external-'+TAG+'.sqlite3')
LOGDIR=ROOT/'logs';LOGDIR.mkdir(exist_ok=True)
PASSED=FAILED=0
procs={}

def check(cond,desc):
 global PASSED,FAILED
 if cond: PASSED+=1;print(f'PASS {PASSED}/{PASSED+FAILED} {desc}',flush=True)
 else:
  FAILED+=1;print(f'FAIL {FAILED}/{PASSED+FAILED} {desc}',flush=True)
  raise AssertionError(desc)
def eventually(fn,seconds=12):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   result=fn()
   if result:return result
  except (OSError,urllib.error.URLError,ValueError):pass
  time.sleep(.12)
 return None

def freeport():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
API=freeport();API2=freeport();PROVIDER=freeport()
JDBC=f'jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema={SCHEMA}'

def sql(stmt):
 p=subprocess.run([PG,'host=127.0.0.1 port=55432 dbname=postgres user=nobody','-X','-A','-t','-v','ON_ERROR_STOP=1','-c',stmt],capture_output=True,text=True,timeout=15)
 if p.returncode:raise RuntimeError(p.stderr+' SQL '+stmt)
 return p.stdout.strip()

def start(name,command,env,marker):
 logfile=LOGDIR/(TAG+'-'+name+'-'+str(len(procs))+'.log')
 f=logfile.open('w')
 p=subprocess.Popen(command,env={**os.environ,**env},stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
 procs[name]=(p,f,logfile)
 found=eventually(lambda:marker in logfile.read_text() or p.poll() is not None,10)
 if not found or p.poll() is not None:raise RuntimeError(name+' startup failed '+logfile.read_text())
 return p

def stop(name,kill=False):
 if name not in procs:return
 p,f,_=procs.pop(name)
 if p.poll() is None:
  (p.kill if kill else p.terminate)()
  try:p.wait(timeout=4)
  except subprocess.TimeoutExpired:p.kill();p.wait(timeout=4)
 f.close()

def provider(override='NONE'):
 return start('provider',['python3','-u',str(ROOT/'provider'/'independent_provider.py')],{
  'P19_PROVIDER_SQLITE':str(SQLITE),'P19_PROVIDER_PORT':str(PROVIDER),
  'P19_PROVIDER_RETENTION_MS':'1500','P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
  'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01','P20_LOOKUP_OVERRIDE':override,
 },'P20_PROVIDER_READY')

def service(crash=False,name='service',port=None):
 return start(name,['java','-cp',CP,'demo.DeroyqueApplication'],{
  'P13_API_PORT':str(port or API),'P13_JDBC':JDBC,'P13_QUEUE':QUEUE,
  'P13_PROVIDER_URL':f'http://127.0.0.1:{PROVIDER}/activate',
  'P19_RECONCILE_URL':f'http://127.0.0.1:{PROVIDER}/lookup',
  'P16_PROVIDER_RETENTION_MS':'0','P20_SIGNED_LOOKUP_REQUIRED':'1',
  'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
  'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
  'APP_API_ENABLED':'1','APP_READ_TOKEN':TOKEN_READ,'APP_WRITE_TOKEN':TOKEN_WRITE,
  'P13_CRASH_AFTER_HTTP_ONCE':'1' if crash else '0'
 },'P13_SERVICE_READY')

def req(method,path,payload=None,token=None,ctype='application/json'):
 data=None if payload is None else (payload if isinstance(payload,bytes) else json.dumps(payload).encode('utf-8'))
 h={}
 if token is not None:h['Authorization']='Bearer '+token
 if data is not None:h['Content-Type']=ctype
 request=urllib.request.Request(f'http://127.0.0.1:{API}'+path,data=data,headers=h,method=method)
 try:
  with urllib.request.urlopen(request,timeout=5) as res:return res.status,res.read().decode(),dict(res.headers)
 except urllib.error.HTTPError as e:return e.code,e.read().decode(),dict(e.headers)

def jreq(method,path,payload=None,token=None,ctype='application/json'):
 status,body,headers=req(method,path,payload,token,ctype)
 try:parsed=json.loads(body)
 except json.JSONDecodeError:parsed=body
 return status,parsed,headers

def stage(rid):
 try:return jreq('GET','/api/v1/requests/'+rid,token=TOKEN_READ)[1].get('status')
 except Exception:return None

def effects(key):
 with sqlite3.connect(SQLITE) as db:return db.execute('SELECT count(*) FROM effect WHERE key=?',(key,)).fetchone()[0]

def run():
 subprocess.run([str(ROOT/'scripts'/'build.sh')],check=True)
 sql('CREATE SCHEMA '+SCHEMA)
 p=subprocess.run(['java','-cp',CP,'demo.JdbcRabbitLedger','org.postgresql.Driver',JDBC,'nobody','','schema'],capture_output=True,text=True,timeout=12)
 check(p.returncode==0 and 'SCHEMA_CREATED' in p.stdout,'single PostgreSQL schema created (core/outbox/policy/evidence)')
 provider();service()
 check(req('GET','/health')[0]==200,'integrated Java worker + RabbitMQ + PostgreSQL online')
 check(jreq('GET','/api/v1/health/readiness')[0]==200,'readiness verifies PostgreSQL and RabbitMQ connections')
 check(jreq('GET','/api/v1/health/liveness')[0]==200,'unprotected liveness endpoint available on loopback')
 check(jreq('GET','/api/v1/requests/x')[0]==401,'GET requires bearer read token')
 check(jreq('POST','/api/v1/requests',{},TOKEN_READ)[0]==401,'write route rejects read-only token')
 check(jreq('GET','/api/v1/requests/x',token=TOKEN_WRITE)[0]==401,'read route rejects write-only token')
 check(req('POST','/submit',b'rid=bad&key=bad&mode=ok',TOKEN_WRITE,'application/x-www-form-urlencoded')[0]==404,'legacy unprotected submit unavailable')
 check(req('GET','/status?rid=bad')[0]==404,'legacy unprotected status unavailable')
 check(jreq('POST','/api/v1/requests',{},TOKEN_WRITE)[0]==400,'required request fields enforced')
 check(jreq('POST','/api/v1/requests',{'externalId':'x'},TOKEN_WRITE)[0]==400,'incomplete request rejected')
 check(req('POST','/api/v1/requests',b'{"externalId":"x","externalId":"y"}',TOKEN_WRITE)[0]==400,'duplicate JSON keys rejected')
 check(req('POST','/api/v1/requests',b'{oops}',TOKEN_WRITE)[0]==400,'malformed JSON rejected')
 check(req('POST','/api/v1/requests',b'{}',TOKEN_WRITE,'text/plain')[0]==415,'media type verified')
 check(req('POST','/api/v1/requests',b' '*4200,TOKEN_WRITE)[0]==413,'oversized body rejected')
 check(jreq('POST','/api/v1/requests',{'externalId':'bad','customerId':'a','productCode':'b','unknown':'x'},TOKEN_WRITE)[0]==400,'unknown properties rejected')
 base={'externalId':'customer-order-'+TAG,'customerId':'customer-ñ','productCode':'trial-premium'}
 status,d,h=jreq('POST','/api/v1/requests',base,TOKEN_WRITE)
 check(status==202 and len(d.get('id',''))==36,'first public request 202 and durable UUID')
 rid=d['id']
 check(h.get('Location')=='/api/v1/requests/'+rid,'Location links to created request')
 status,replay,_=jreq('POST','/api/v1/requests',base,TOKEN_WRITE)
 check(status==200 and replay.get('id')==rid and replay.get('idempotentReplay'),'identical replay returns same UUID (200)')
 check(jreq('POST','/api/v1/requests',{**base,'productCode':'other'},TOKEN_WRITE)[0]==409,'conflicting replay returns 409 without new work')
 check(jreq('POST','/api/v1/requests',{**base,'customerId':'someone'},TOKEN_WRITE)[0]==409,'customer mismatch on same external ID rejected')
 check(eventually(lambda:stage(rid)=='DONE',14),'end-to-end activation completed using signed provider report')
 status,d,_=jreq('GET','/api/v1/requests/'+rid,token=TOKEN_READ)
 check(status==200 and d['externalId']==base['externalId'] and d['customerId']==base['customerId'] and d['productCode']==base['productCode'],'GET returns persisted immutable business data')
 check(d['attempts']==1,'exactly one durable authorization')
 check(d['signedEvidenceCount']>=1,'signed lookup evidence durably attached')
 check(d['evidenceIncidents']==0,'no incident in normal run')
 check(effects(base['externalId'])==1,'one independent SQLite provider effect')
 check(int(sql(f"SELECT count(*) FROM {SCHEMA}.p9_requests WHERE idem_key='{base['externalId']}'"))==1,'one request row for idempotency key')
 check(int(sql(f"SELECT count(*) FROM {SCHEMA}.p9_outbox WHERE rid='{rid}'"))>=1,'transactional notification outbox exists')
 check(int(sql(f"SELECT count(*) FROM {SCHEMA}.p9_observation o JOIN {SCHEMA}.p9_dispatch d USING(did) WHERE d.rid='{rid}' AND o.source='SIGNED_PROVIDER_LOOKUP'"))>=1,'pinned policy signed observation and dispatch linked')
 # Concurrency with same idempotency identity; one 202, N-1 200, exactly one effect.
 case={'externalId':'parallel-'+TAG,'customerId':'same','productCode':'plus'}
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
  responses=list(pool.map(lambda _:jreq('POST','/api/v1/requests',case,TOKEN_WRITE),range(12)))
 check(sorted(x[0] for x in responses)==[200]*11+[202],'12 simultaneous POSTs yield exactly one 202, eleven 200')
 ids={x[1].get('id') for x in responses}
 check(len(ids)==1 and None not in ids,'concurrent admissions converged on one immutable ID')
 concurrentId=ids.pop()
 check(eventually(lambda:stage(concurrentId)=='DONE',14),'one concurrent admission completes')
 check(effects(case['externalId'])==1,'concurrent requests caused one external effect')
 check(int(sql(f"SELECT count(*) FROM {SCHEMA}.p9_outbox WHERE nid='initial:{concurrentId}'"))==1,'one initial outbox event despite racing admissions')
 # Different independent keys execute concurrently through the same real broker/worker pool.
 batch=[{'externalId':f'batch-{TAG}-{n}','customerId':'cohort','productCode':'p-'+str(n)} for n in range(6)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  results=list(pool.map(lambda b:jreq('POST','/api/v1/requests',b,TOKEN_WRITE),batch))
 check(all(x[0]==202 for x in results),'six distinct requests admitted concurrently')
 check(eventually(lambda:all(stage(x[1]['id'])=='DONE' for x in results),18),'six independently processed requests reach DONE')
 check(all(effects(b['externalId'])==1 for b in batch),'six distinct durable external effects exactly once in this run')
 # Two independent Java application JVMs subscribe to the same real RabbitMQ quorum queue;
 # a single PostgreSQL State Store arbitrates ownership and dispatch.
 second=service(name='service2',port=API2)
 check(second.poll() is None,'second independent worker JVM online with same durable ledger and RabbitMQ queue')
 batch2=[{'externalId':f'distributed-{TAG}-{n}','customerId':'cross-jvm','productCode':'subscription'} for n in range(14)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
  results2=list(pool.map(lambda b:jreq('POST','/api/v1/requests',b,TOKEN_WRITE),batch2))
 check(all(x[0]==202 for x in results2),'fourteen requests admitted while two separate JVMs consume')
 check(eventually(lambda:all(stage(x[1]['id'])=='DONE' for x in results2),24),'fourteen jobs completed with two JVMs competing for messages')
 check(all(effects(b['externalId'])==1 for b in batch2),'fourteen externally persisted effects, one per distinct key')
 check(second.poll() is None,'second JVM remained alive after parallel processing')
 stop('service2')
 # Real abrupt JVM crash AFTER external POST but BEFORE durable observation.
 stop('service');crash=service(crash=True)
 crashed={'externalId':'crash-'+TAG,'customerId':'c','productCode':'after-http'}
 response=jreq('POST','/api/v1/requests',crashed,TOKEN_WRITE)
 check(response[0]==202,'crash-case request admitted before worker exit')
 crashId=response[1]['id']
 check(eventually(lambda:crash.poll() is not None,9),'JVM stopped after provider HTTP response before observation')
 check(crash.returncode==82,'controlled JVM hard crash took the expected path')
 check(effects(crashed['externalId'])==1,'provider SQLite effect survived worker process crash')
 stop('service');service()
 check(eventually(lambda:stage(crashId)=='DONE',16),'new JVM reconciles prior effect via signed independent lookup')
 check(effects(crashed['externalId'])==1,'no duplicate effect created during recovery/reconciliation')
 check(int(sql(f"SELECT count(*) FROM {SCHEMA}.p20_signed_attestation a JOIN {SCHEMA}.p9_dispatch d USING(did) WHERE d.rid='{crashId}'"))>=1,'signed reconciliation proof survives in PostgreSQL')
 # Provider outage after a successful request: persisted business result survives provider disappearance.
 stop('provider')
 check(stage(rid)=='DONE','terminal state readable even when external provider offline')
 check(jreq('GET','/api/v1/health/readiness')[0]==200,'provider outage alone does not make DB/broker readiness DOWN')
 provider()
 check(effects(base['externalId'])==1,'provider SQLite effects remain durable after provider restart')
 print(f'FINAL: FAILED {FAILED}/{PASSED+FAILED}; PASSED {PASSED}/{PASSED+FAILED}',flush=True)

if __name__=='__main__':
 try:run()
 except BaseException as ex:
  print(f'FINAL: FAILED {FAILED}/{PASSED+FAILED}; PASSED {PASSED}/{PASSED+FAILED} ERROR {type(ex).__name__}: {ex}',flush=True)
  raise
 finally:
  for name in list(procs):stop(name,kill=True)
  # Keep the isolated schema/SQLite for deterministic postmortem; never touch shared DBs or Rabbit queues.
  print('LAB SCHEMA',SCHEMA,'PROVIDER SQLITE',SQLITE,flush=True)
