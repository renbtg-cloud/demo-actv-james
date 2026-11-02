#!/usr/bin/env python3
"""P19 separated provider fault experiment, disposable PostgreSQL schemas and Rabbit queues only.
Provider state: independent SQLite file. No PostgreSQL query participates in the provider effect.
"""
import os, sqlite3, time, subprocess, pathlib, uuid, urllib.request, urllib.parse, urllib.error, signal
ROOT=pathlib.Path(__file__).resolve().parent
PG='/mnt/data/infra-lab/pgsql/usr/lib/postgresql/17/bin/psql'
CP=f'{ROOT.parents[1]}/build/classes:{os.environ.get("POSTGRES_JAR","/mnt/data/postgresql-42.7.14.jar")}:{os.environ.get("RABBITMQ_CLIENT_JAR","/mnt/data/amqp-client-5.37.0.jar")}'
DB='host=127.0.0.1 port=55432 user=nobody dbname=postgres'
SVC_PORT=18781; PROVIDER_PORT=18782
COUNTS={'passed':0,'failed':0}
RUN=uuid.uuid4().hex[:7]
LOGS=ROOT/'logs'; LOGS.mkdir(exist_ok=True)

def check(ok,reason):
    if not ok:COUNTS['failed']+=1;print(f'FAIL {COUNTS["failed"]}/{sum(COUNTS.values())} {reason}',flush=True);raise AssertionError(reason)
    COUNTS['passed']+=1;print(f'PASS {COUNTS["passed"]}/{sum(COUNTS.values())} {reason}',flush=True)

def sql(statement):
    r=subprocess.run([PG,DB,'-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],capture_output=True,text=True,timeout=12)
    if r.returncode:raise RuntimeError(r.stderr+' QUERY '+statement)
    return r.stdout.strip()

def until(f,timeout=11):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            if f():return True
        except Exception:pass
        time.sleep(.09)
    return False

def req(port,path,form=None,timeout=2):
    data=urllib.parse.urlencode(form).encode() if form is not None else None
    request=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data,method='POST' if form is not None else 'GET')
    try:
        with urllib.request.urlopen(request,timeout=timeout) as r:return r.status,r.read().decode(),dict(r.headers)
    except urllib.error.HTTPError as e:return e.code,e.read().decode(),dict(e.headers)

class Scenario:
    def __init__(self,label,ttl=1000,miss=False):
        self.label=label;self.sqlite=ROOT/(f'provider-{label}-{RUN}.sqlite3')
        self.schema=f'deroyque_p19_{label.replace("-","_")}_{RUN}'
        self.jdbc=f'jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema={self.schema}'
        self.queue=f'deroyque.p19.{label}.{RUN}'
        self.ttl=ttl;self.miss=miss;self.procs={}
        sql(f'CREATE SCHEMA {self.schema}')
        cmd=['java','-cp',CP,'demo.JdbcRabbitLedger','org.postgresql.Driver',self.jdbc,'nobody','','schema']
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=12)
        check(r.returncode==0 and 'SCHEMA_CREATED' in r.stdout,label+' independent worker schema created')
    def spawn(self,name,cmd,env,ready):
        logfile=LOGS/f'{self.label}-{name}-{len(self.procs)}.log'
        fh=logfile.open('w')
        proc=subprocess.Popen(cmd,env={**os.environ,**env},stdout=fh,stderr=subprocess.STDOUT,start_new_session=True)
        self.procs[name]=(proc,fh,logfile)
        if not until(lambda:ready in logfile.read_text() or proc.poll() is not None,9) or proc.poll() is not None:
            raise RuntimeError('startup failed '+name+' '+logfile.read_text())
        return proc
    def provider(self,miss=None,forge=False):
        if miss is None:miss=self.miss
        return self.spawn('provider',['python3','-u',str(ROOT/'provider_sqlite.py')],{
            'P19_PROVIDER_PORT':str(PROVIDER_PORT),'P19_PROVIDER_SQLITE':str(self.sqlite),
            'P19_PROVIDER_RETENTION_MS':str(self.ttl),'P19_LOOKUP_FORCE_MISS':'1' if miss else '0',
            'P19_LOOKUP_FORGE_RECEIPT':'1' if forge else '0'},'P19_PROVIDER_READY')
    def service(self,extra=None):
        return self.spawn('service',['java','-cp',CP,'demo.P13Service'],{
            'P13_API_PORT':str(SVC_PORT),'P13_JDBC':self.jdbc,'P13_QUEUE':self.queue,
            'P13_PROVIDER_URL':f'http://127.0.0.1:{PROVIDER_PORT}/activate',
            'P19_RECONCILE_URL':f'http://127.0.0.1:{PROVIDER_PORT}/lookup',
            'P16_PROVIDER_RETENTION_MS':'0',**(extra or {})},'P13_SERVICE_READY')
    def stop(self,name):
        if name not in self.procs:return
        p,fh,path=self.procs.pop(name)
        if p.poll() is None:p.kill()
        p.wait(timeout=4);fh.close()
    def close(self):
        for name in list(self.procs):self.stop(name)
    def submit(self,mode):
        rid='P19_'+RUN+'_'+self.label.replace('-','_')[:18]
        key='K_'+rid
        status,body,_=req(SVC_PORT,'/submit',{'rid':rid,'key':key,'mode':mode})
        check((status,body)==(202,'COMMITTED'),self.label+' work admitted')
        return rid,key
    def stage(self,rid):return sql(f"SELECT stage FROM {self.schema}.p9_requests WHERE rid='{rid}'")
    def attempts(self,rid):return int(sql(f"SELECT attempts FROM {self.schema}.p9_requests WHERE rid='{rid}'"))
    def sources(self,rid):return sql(f"SELECT source||':'||action FROM {self.schema}.p9_observation o JOIN {self.schema}.p9_dispatch d USING(did) WHERE d.rid='{rid}' ORDER BY oid")
    def effects(self,key):
        with sqlite3.connect(self.sqlite) as db:return db.execute('SELECT count(*) FROM effect WHERE key=?',(key,)).fetchone()[0]
    def health(self):return req(SVC_PORT,'/health')[0]==200


def case_positive(label,mode):
    s=Scenario(label,ttl=900)
    try:
        s.provider();s.service()
        check(s.health(),label+' Java, RabbitMQ, PostgreSQL operational')
        rid,key=s.submit(mode)
        check(until(lambda:s.stage(rid)=='DONE',8),label+' reconciled positive provider lookup')
        check(s.attempts(rid)==1,label+' only one authorized attempt')
        check(s.effects(key)==1,label+' only one durable independent SQLite effect')
        check('PROVIDER_LOOKUP:ADOPT_ACTIVATED' in s.sources(rid),label+' lookup source durably attributed')
        rec=sql(f"SELECT oid||'|'||did||'|'||http_status||'|'||body FROM {s.schema}.p9_observation LIMIT 1").split('|')
        def replay(source):
            args=['java','-cp',CP,'demo.JdbcRabbitLedger','org.postgresql.Driver',s.jdbc,'nobody','','observe_http_source',*rec,source]
            res=subprocess.run(args,capture_output=True,text=True,timeout=8)
            return res.returncode,res.stdout.strip()
        check(replay('PROVIDER_LOOKUP')==(0,'ALREADY'),label+' replay with same evidence AND source idempotent')
        check(replay('HTTP_RESPONSE')==(0,'CONFLICT'),label+' changed provenance rejected under same observation ID')
        time.sleep(1.0)
        check(s.effects(key)==1,label+' no duplicate after key expiry')
    finally:s.close()

def case_crash():
    label='crash-provider';s=Scenario(label,ttl=850)
    try:
        p=s.provider();s.service()
        rid,key=s.submit('crash')
        check(until(lambda:p.poll() is not None,6),label+' provider JVM-independent process crashed after COMMIT')
        check(p.returncode==78,label+' crash injected after effect fsync/COMMIT')
        s.stop('provider')
        check(s.effects(key)==1,label+' separate SQLite effect survived provider crash')
        check(until(lambda:s.stage(rid)=='PAUSED',11),label+' worker safely paused with unknown provider result')
        check(s.attempts(rid)==1,label+' no unproven retry during outage')
        time.sleep(1.0)
        s.provider()
        check(until(lambda:s.stage(rid)=='DONE',9),label+' late query after provider restart adopted confirmed effect')
        check(s.effects(key)==1,label+' no duplicate after retention expiry and restart')
        check('PROVIDER_LOOKUP:ADOPT_ACTIVATED' in s.sources(rid),label+' durable provenance of late reconciliation')
    finally:s.close()

def case_stale_negative():
    label='stale-negative';s=Scenario(label,ttl=900,miss=True)
    try:
        s.provider();s.service()
        rid,key=s.submit('after500')
        check(until(lambda:s.effects(key)==1,5),label+' effect committed in independent provider store')
        check(until(lambda:s.stage(rid)=='PAUSED',11),label+' negative lookup does not authorize unsafe retry')
        check(s.attempts(rid)==1,label+' stale 404 is NOT proof of no effect')
        check('ADOPT_ACTIVATED' not in s.sources(rid),label+' no fabricated positive observation')
        s.stop('provider');s.provider(miss=False)
        check(until(lambda:s.stage(rid)=='DONE',9),label+' later positive lookup can reconcile paused request')
        check(s.effects(key)==1,label+' no duplicate on lookup recovery')
    finally:s.close()

def case_multiple():
    label='multi-effects';s=Scenario(label,ttl=300)
    try:
        s.provider()
        rid='P19_'+RUN+'_'+label.replace('-','_');key='K_'+rid
        for n in range(2):
            code,_,_=req(PROVIDER_PORT,'/activate',{'key':key,'mode':'ok'})
            check(code==200,label+f' provider seed effect {n+1} committed')
            time.sleep(.45)
        check(s.effects(key)==2,label+' 2 actual external effects across TTL')
        status,body,_=req(PROVIDER_PORT,'/lookup?key='+urllib.parse.quote(key))
        check(status==409 and 'MULTIPLE_EFFECTS' in body,label+' query refuses ambiguous effect history')
        s.service()
        code,body,_=req(SVC_PORT,'/submit',{'rid':rid,'key':key,'mode':'after500'})
        check((code,body)==(202,'COMMITTED'),label+' accepted request with compromised external key')
        check(until(lambda:s.stage(rid)=='PAUSED',7),label+' worker paused instead of treating ambiguity as success')
        check(s.effects(key)>=3,label+' control exposed further unsafe provider effect')
        check('ADOPT_ACTIVATED' not in s.sources(rid),label+' no falsely adopted success after multiple effects')
    finally:s.close()

def case_forgery():
    label='forged-lookup';s=Scenario(label,ttl=900)
    try:
        s.provider(forge=True);s.service()
        rid,key=s.submit('after500')
        check(until(lambda:s.stage(rid)=='PAUSED',8),label+' forged positive response causes protocol pause')
        check(s.effects(key)==1,label+' actual effect exists despite forged lookup')
        check('ADOPT_ACTIVATED' not in s.sources(rid),label+' no false positive adoption')
        check(int(sql(f"SELECT count(*) FROM {s.schema}.p14_evidence_incident WHERE rid='{rid}'"))==1,label+' independent incident recorded')
        check('PROVIDER_LOOKUP:PAUSE_PROTOCOL' in s.sources(rid),label+' source persisted as provider lookup')
    finally:s.close()

def case_noeffect():
    label='no-effect';s=Scenario(label,ttl=900)
    try:
        s.provider();s.service();rid,key=s.submit('before500')
        check(until(lambda:s.stage(rid)=='PAUSED',11),label+' no-effect uncertain result paused')
        check(s.effects(key)==0,label+' no external effect in independent journal')
        check(s.attempts(rid)==1,label+' safe policy avoids automatic retry without retention guarantee')
        check('ADOPT_' not in s.sources(rid),label+' missing effect not mistaken for verified effect or rejection')
    finally:s.close()

if __name__=='__main__':
    try:
        case_positive('after-503','after500')
        case_positive('dropped-response','drop')
        case_crash()
        case_stale_negative()
        case_multiple()
        case_forgery()
        case_noeffect()
        print(f'P19 SUMMARY FAILURES {COUNTS["failed"]}/{sum(COUNTS.values())}; PASSED {COUNTS["passed"]}/{sum(COUNTS.values())}',flush=True)
    except BaseException as e:
        print(f'P19 SUMMARY FAILURES {COUNTS["failed"]}/{sum(COUNTS.values())}; PASSED {COUNTS["passed"]}/{sum(COUNTS.values())}; ERROR {e!r}',flush=True)
        raise
