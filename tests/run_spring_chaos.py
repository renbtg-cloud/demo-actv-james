#!/usr/bin/env python3
"""Adversarial REAL Spring Boot integration: competing recovery, dependency outages and signed evidence.
Each scenario uses disposable PostgreSQL schema, RabbitMQ queue and independent SQLite provider.
Not production security tests; injected failures and a deliberately dishonest signed issuer are explicit.
"""
import argparse, json, os, pathlib, secrets, signal, socket, sqlite3, subprocess, tempfile, time, urllib.error, urllib.request, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
P=argparse.ArgumentParser()
P.add_argument('--boot-jar',required=True)
P.add_argument('--scenario',required=True,choices=['competing-windows','dependency-interruptions','late-and-contradiction'])
P.add_argument('--output',type=pathlib.Path)
ARGS=P.parse_args()
PG={k:os.getenv('DEROYQUE_PG_'+k.upper(),default) for k,default in [('host','127.0.0.1'),('port','5432'),('db','deroyque_ci'),('user','deroyque_ci'),('password','')]}
TOKEN_READ=secrets.token_urlsafe(30);TOKEN_WRITE=secrets.token_urlsafe(30)
TAG=uuid.uuid4().hex[:9];TMP=tempfile.TemporaryDirectory(prefix='deroyque-chaos-');DIR=pathlib.Path(TMP.name)
RESULT={'scenario':ARGS.scenario,'checks':[],'cases':[]};CHILDREN=[];PAUSED=[]

def check(ok,name):
    ok=bool(ok); RESULT['checks'].append({'name':name,'passed':ok}); n=len(RESULT['checks'])
    print(f'{"PASS" if ok else "FAIL"} {n}/{n} {name}',flush=True)
    if not ok:raise AssertionError(name)

def sql(command):
    args=[os.getenv('DEROYQUE_PSQL','psql'),'-X','-A','-t','-v','ON_ERROR_STOP=1','-h',PG['host'],'-p',PG['port'],'-U',PG['user'],'-d',PG['db'],'-c',command]
    p=subprocess.run(args,env={**os.environ,'PGPASSWORD':PG['password']},capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError('postgres command exit '+str(p.returncode))
    return p.stdout.strip()

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def spawn(role,cmd,env=None):
    fh=(DIR/(role+'.log')).open('w');p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=fh,stderr=subprocess.STDOUT,start_new_session=True)
    CHILDREN.append((role,p,fh));return p

def stop(p,hard=False):
    if p.poll() is None:
        try:os.killpg(p.pid,signal.SIGKILL if hard else signal.SIGTERM)
        except ProcessLookupError:pass
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=4)

def until(f,seconds=40):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            v=f()
            if v:return v
        except (OSError,ValueError,sqlite3.OperationalError,urllib.error.URLError,subprocess.TimeoutExpired):pass
        time.sleep(.2)
    return None

def http(url,verb='GET',body=None,token=None):
    h={}
    if token:h['Authorization']='Bearer '+token
    if body is not None:
        body=json.dumps(body).encode();h['Content-Type']='application/json'
    req=urllib.request.Request(url,method=verb,data=body,headers=h)
    try:
        with urllib.request.urlopen(req,timeout=4) as r:return r.status,r.read().decode(),dict(r.headers)
    except urllib.error.HTTPError as e:return e.code,e.read().decode(),dict(e.headers)

def server_url(p):return f'http://127.0.0.1:{p}'

def boot_env(schema,queue,server_port,provider_port):
    url=f'jdbc:postgresql://{PG["host"]}:{PG["port"]}/{PG["db"]}?currentSchema={schema}'
    return {
      'P13_JDBC':url,'P13_DB_USER':PG['user'],'P13_DB_PASSWORD':PG['password'],
      'SPRING_DATASOURCE_URL':url,'SPRING_DATASOURCE_USERNAME':PG['user'],'SPRING_DATASOURCE_PASSWORD':PG['password'],
      'SPRING_FLYWAY_SCHEMAS':schema,'SPRING_FLYWAY_DEFAULT_SCHEMA':schema,
      'P13_QUEUE':queue,'P13_API_PORT':str(server_port),'SERVER_PORT':str(server_port),'SERVER_ADDRESS':'127.0.0.1',
      'P13_PROVIDER_URL':server_url(provider_port)+'/activate','P19_RECONCILE_URL':server_url(provider_port)+'/lookup',
      'P16_PROVIDER_RETENTION_MS':'300000','P20_SIGNED_LOOKUP_REQUIRED':'1',
      'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
      'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
      'APP_READ_TOKEN':TOKEN_READ,'APP_WRITE_TOKEN':TOKEN_WRITE,'APP_AUTH_MODE':'lab','APP_API_ENABLED':'1','APP_LAB_METRICS_ENABLED':'0'
    }

def provider(role,portnum,override='NONE'):
    return spawn(role,['python3','-u',str(ROOT/'provider/independent_provider.py')],{
      'P19_PROVIDER_PORT':str(portnum),'P19_PROVIDER_SQLITE':str(DIR/'provider.sqlite3'),
      'P19_PROVIDER_RETENTION_MS':'600000','P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
      'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01','P20_LOOKUP_OVERRIDE':override})

def ensure_provider(proc,pt):
    check(bool(until(lambda:proc.poll() is None and http(server_url(pt)+'/health')[0]==200,15)),'provider SQLite independently ready')

def boot(role,env,jar):
    portnum=int(env['SERVER_PORT']);p=spawn(role,['java','-Xmx320m','-jar',str(jar)],env)
    check(bool(until(lambda:p.poll() is None and http(server_url(portnum)+'/api/v1/health/readiness')[0]==200,60)),role+' Spring Boot JVM ready')
    return p

def observe(schema,key):
    line=sql(f"SELECT r.rid,r.stage,(SELECT count(*) FROM {schema}.p9_dispatch d WHERE d.rid=r.rid),"
         f"(SELECT count(*) FROM {schema}.p9_observation o JOIN {schema}.p9_dispatch d ON d.did=o.did WHERE d.rid=r.rid),"
         f"(SELECT count(*) FROM {schema}.p20_signed_attestation a JOIN {schema}.p9_dispatch d ON d.did=a.did WHERE d.rid=r.rid),"
         f"(SELECT count(*) FROM {schema}.p9_settlement s WHERE s.rid=r.rid) FROM {schema}.p9_requests r WHERE r.idem_key='{key}'")
    if not line:return None
    v=line.split('|');return dict(rid=v[0],stage=v[1],dispatches=int(v[2]),observations=int(v[3]),signed=int(v[4]),settlements=int(v[5]))

def effects(key):
    with sqlite3.connect(DIR/'provider.sqlite3',timeout=10) as c:
        return c.execute('SELECT count(*) FROM effect WHERE key=?',(key,)).fetchone()[0]

def posts(key):return (DIR/'provider.log').read_text(errors='replace').count('DURABLE_EFFECT '+key+' generation=')

def admit(pt,key):
    payload={'externalId':key,'customerId':'customer-chaos','productCode':'product-chaos'}
    st,body,_=http(server_url(pt)+'/api/v1/requests','POST',payload,TOKEN_WRITE)
    check(st==202 and len(json.loads(body).get('id',''))==36,key+': durable HTTP 202 admission')
    return payload,json.loads(body)['id']

def new_case(label):
    schema='m16_'+uuid.uuid4().hex[:12];q='m16.'+uuid.uuid4().hex[:14];sql('CREATE SCHEMA '+schema)
    return schema,q,f'm16-{TAG}-{label}'

def no_incidents(schema,key):
    return sql(f"SELECT count(*) FROM {schema}.p14_evidence_incident WHERE rid=(SELECT rid FROM {schema}.p9_requests WHERE idem_key='{key}')")=='0'

def competing(jar):
    pp=port();provider_proc=provider('provider',pp);ensure_provider(provider_proc,pp)
    marks={'after-auth':('P13_CRASH_AFTER_AUTH_ONCE',81,0,0),
           'after-http':('P13_CRASH_AFTER_HTTP_ONCE',82,0,1),
           'after-observe':('P14_CRASH_AFTER_OBSERVE_ONCE',83,1,1)}
    for mode,(flag,exit_code,signed,eff) in marks.items():
        schema,q,key=new_case(mode);pt=port();env=boot_env(schema,q,pt,pp)
        victim=boot('victim-'+mode,{**env,flag:'1'},jar)
        payload,rid=admit(pt,key)
        check(bool(until(lambda:victim.poll() is not None,40)),mode+': victim really died')
        check(victim.returncode==exit_code,mode+': exact Runtime.halt exit '+str(exit_code))
        at=observe(schema,key)
        check(at and at['stage']=='IN_FLIGHT' and at['signed']==signed and at['settlements']==0,
              mode+': durable state at crash verified before recovery')
        check(effects(key)==eff,mode+': durable external effect count at crash')
        previous_posts=posts(key)
        # Start all recovery JVMs before waiting on any, so they race over the SAME PostgreSQL and RabbitMQ.
        peers=[]
        for i in range(3):
            sp=port();role=f'recovery-{mode}-{i}';p=spawn(role,['java','-Xmx320m','-jar',str(jar)],
                 boot_env(schema,q,sp,pp))
            peers.append((role,p,sp))
        check(len({p.pid for _,p,_ in peers})==3 and all(p.poll() is None for _,p,_ in peers),
              mode+': three independent recovering Spring JVM processes launched concurrently')
        for role,p,sp in peers:
            check(bool(until(lambda p=p,sp=sp:p.poll() is None and http(server_url(sp)+'/api/v1/health/readiness')[0]==200,60)),
                  mode+': '+role+' ready on same database and queue')
        final=until(lambda:observe(schema,key) if (observe(schema,key) or {}).get('stage')=='DONE' else None,90)
        if final is None:
            snapshot=observe(schema,key)
            print('COMPETING_RECOVERY_DIAG '+json.dumps({'window':mode,'state':snapshot,'effects':effects(key),
                 'peer_liveness':[p.poll() is None for _,p,_ in peers]},sort_keys=True),flush=True)
        check(final and final['signed']>=1 and final['settlements']>=1,mode+': concurrent recovery reached DONE with signed settlement')
        check(effects(key)==1 and no_incidents(schema,key),mode+': one effect, no contradictory incidents')
        if mode=='after-observe':check(posts(key)==previous_posts,mode+': no provider POST after durable signed proof')
        check(all(p.poll() is None for _,p,_ in peers),mode+': all three surviving peer JVMs remain live')
        replay=http(server_url(peers[0][2])+'/api/v1/requests','POST',payload,TOKEN_WRITE)
        check(replay[0]==200 and json.loads(replay[1]).get('id')==rid,mode+': replay preserved UUID')
        RESULT['cases'].append({'window':mode,'exit':exit_code,'recovering_peers':3,'effect_at_crash':eff,'effect_final':effects(key),'final_stage':final['stage']})
        for _,p,_ in peers:stop(p)

def container_id(image):
    p=subprocess.run(['docker','ps','--format','{{.ID}} {{.Image}}'],capture_output=True,text=True,timeout=12,check=True)
    matches=[line.split(' ')[0] for line in p.stdout.splitlines() if image in line]
    if len(matches)!=1:raise RuntimeError('Expected exactly one ephemeral CI container for '+image)
    return matches[0]

def pause(name,seconds):
    id=container_id(name)
    subprocess.run(['docker','pause',id],check=True,timeout=15,stdout=subprocess.DEVNULL);PAUSED.append(id)
    print('DEPENDENCY_PAUSED '+name,flush=True)
    time.sleep(seconds)
    subprocess.run(['docker','unpause',id],check=True,timeout=15,stdout=subprocess.DEVNULL)
    PAUSED.remove(id)
    print('DEPENDENCY_RESTORED '+name,flush=True)

def interruptions(jar):
    pp=port();pr=provider('provider',pp);ensure_provider(pr,pp)
    for dep,image in [('postgres','postgres:17'),('rabbitmq','rabbitmq:4.4.0-management')]:
        schema,q,key=new_case(dep);pt=port();env=boot_env(schema,q,pt,pp)
        # Delay observation after real provider effect; hold means the request is still IN_FLIGHT during outage.
        peers=[]
        for i in range(2):
            sp=pt if i==0 else port();p=boot(dep+'-worker-'+str(i),{**boot_env(schema,q,sp,pp),'P14_HOLD_BEFORE_OBSERVE_MS':'6500'},jar)
            peers.append((p,sp))
        payload,rid=admit(peers[0][1],key)
        check(bool(until(lambda:effects(key)==1 and (observe(schema,key) or {}).get('stage')=='IN_FLIGHT',35)),
              dep+': effect committed and request IN_FLIGHT before outage')
        pause(image,3)
        check(all(p.poll() is None for p,_ in peers),dep+': both Spring JVMs survived actual container pause/unpause')
        check(bool(until(lambda:all(http(server_url(sp)+'/api/v1/health/readiness')[0]==200 for _,sp in peers),35)),
              dep+': both Spring JVMs regained dependency readiness')
        final=until(lambda:observe(schema,key) if (observe(schema,key) or {}).get('stage')=='DONE' else None,100)
        check(final and final['signed']>=1,dep+': signed evidence recovered and request became DONE')
        check(effects(key)==1 and no_incidents(schema,key),dep+': exactly one effect after outage')
        code,body,_=http(server_url(peers[1][1])+'/api/v1/requests','POST',payload,TOKEN_WRITE)
        check(code==200 and json.loads(body)['id']==rid,dep+': idempotency replay on other JVM')
        RESULT['cases'].append({'outage':dep,'paused_seconds':3,'peers':2,'final_stage':final['stage'],'effects':effects(key)})
        for p,_ in peers:stop(p)

def late_conflict(jar):
    pp=port();pr=provider('provider',pp,'UNSIGNED');ensure_provider(pr,pp)
    schema,q,key=new_case('late');pt=port();env=boot_env(schema,q,pt,pp)
    peers=[]
    for i in range(2):
        sp=pt if i==0 else port();p=boot('late-worker-'+str(i),boot_env(schema,q,sp,pp),jar);peers.append((p,sp))
    payload,rid=admit(pt,key)
    check(bool(until(lambda:effects(key)==1 and (observe(schema,key) or {}).get('stage')=='PAUSED',35)),
          'unsigned signed-lookup response conservatively PAUSED despite durable external effect')
    initial=observe(schema,key)
    check(initial['signed']==0 and effects(key)==1,'late proof not yet signed or accepted')
    stop(pr);pr=provider('provider',pp);ensure_provider(pr,pp)
    final=until(lambda:observe(schema,key) if (observe(schema,key) or {}).get('stage')=='DONE' else None,70)
    check(final and final['signed']>=1 and final['settlements']>=1,'late authentic signed proof adopted by running Spring peer')
    check(effects(key)==1 and no_incidents(schema,key),'late proof generated no additional external effects or incidents')
    RESULT['cases'].append({'late_signed_proof':'PAUSED-to-DONE','effects':effects(key)})
    # Deliberately dishonest but cryptographically valid signer, not a forged signature.
    stop(pr);pr=provider('provider',pp,'REJECTED');ensure_provider(pr,pp)
    # Use the SAME verifier and JDBC store routines as production, from a standalone test-only Java main.
    query=sql(f"SELECT d.did FROM {schema}.p9_dispatch d JOIN {schema}.p9_requests r ON d.rid=r.rid WHERE r.idem_key='{key}' ORDER BY d.did LIMIT 1")
    check(bool(query) and len(query)==36,'original dispatch recorded for contradictory proof')
    cp=os.pathsep.join([str(ROOT/'adapter-springboot/target/test-classes'),str(ROOT/'adapter-springboot/target/classes'),os.environ['DEROYQUE_CHAOS_PG_JAR']])
    proc=subprocess.run(['java','-cp',cp,'demo.SignedContradictionProbe',query,key,server_url(pp)+'/activate',server_url(pp)+'/lookup'],
       env={**os.environ,**boot_env(schema,q,pt,pp)},text=True,capture_output=True,timeout=25)
    check(proc.returncode==0 and 'SIGNED_CONTRADICTION_PERSISTED' in proc.stdout,
          'Java ledger independently verifies and durably stores genuine contradictory signed provider claim')
    # The watchdog on either peer will process contradictory evidence after it is recorded.
    check(bool(until(lambda:sql(f"SELECT kind FROM {schema}.p14_evidence_incident WHERE rid='{rid}'")=='CONTRADICTORY_PROOF',35)),
          'contradiction detected by live watchdog and incident permanently journaled')
    check((observe(schema,key) or {}).get('stage')=='DONE','previous terminal DONE not silently overwritten by contradictory signed proof')
    check(effects(key)==1,'dishonest signer did not create a second external effect')
    check(sql(f"SELECT count(*) FROM {schema}.p20_signed_attestation WHERE reported_outcome='REJECTED'")=='1',
          'contrary signed evidence retained rather than discarded')
    RESULT['cases'].append({'contradiction':'detected-and-immutable','effects':effects(key),'terminal':'DONE'})
    for p,_ in peers:stop(p)

def main():
    code=1
    try:
        jar=pathlib.Path(ARGS.boot_jar).resolve()
        if not jar.is_file() or jar.stat().st_size<100000:
            print('BLOCKED: actual Spring Boot executable JAR required',flush=True);return 3
        {'competing-windows':competing,'dependency-interruptions':interruptions,'late-and-contradiction':late_conflict}[ARGS.scenario](jar)
        code=0
    except Exception as ex:
        RESULT['error_class']=type(ex).__name__
        print('CHAOS_ERROR '+type(ex).__name__+' (raw sensitive logs suppressed)',flush=True)
    finally:
        for id in reversed(PAUSED):
            try:subprocess.run(['docker','unpause',id],timeout=10,capture_output=True)
            except Exception:pass
        for role,p,f in reversed(CHILDREN):
            stop(p);f.close()
        failures=sum(not x['passed'] for x in RESULT['checks']);passed=len(RESULT['checks'])-failures
        RESULT.update({'failed':failures,'passed':passed,'total':len(RESULT['checks']),'exit':code})
        print(f'CHAOS_RESULT FAILURES {failures}/{len(RESULT["checks"])} PASSED {passed}/{len(RESULT["checks"])} SCENARIO {ARGS.scenario}',flush=True)
        if ARGS.output:
            ARGS.output.parent.mkdir(parents=True,exist_ok=True)
            ARGS.output.write_text(json.dumps(RESULT,indent=2)+'\n')
        TMP.cleanup()
    return code

if __name__=='__main__':raise SystemExit(main())
