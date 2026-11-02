#!/usr/bin/env python3
"""Real infrastructure acceptance harness, no Spring API stand-ins.

Default mode `boot` REQUIRES a compiled executable Spring Boot JAR, then
checks Flyway migrations that Spring must apply to a fresh PostgreSQL schema.
`legacy` is only a control to validate this harness against the JDK adapter;
it is never a substitute for a Boot runtime result.
PostgreSQL/RabbitMQ servers are never stopped or dropped.
"""
import argparse, json, os, pathlib, secrets, signal, socket, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid

ROOT=pathlib.Path(__file__).resolve().parents[1]
PARSER=argparse.ArgumentParser(description=__doc__)
PARSER.add_argument('--mode',choices=['boot','legacy'],default='boot')
PARSER.add_argument('--boot-jar',default=str(ROOT/'adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar'))
PARSER.add_argument('--output',default=None,help='Write sanitized JSON results here')
PARSER.add_argument('--load-jvms',type=int,default=8,help='Number of independent generator JVMs (0 disables additional load)')
PARSER.add_argument('--adversarial-max-jvms',type=int,default=0,help='Run multi-Spring-JVM crash/load test, up to this many independent producer JVMs (0 disables)')
ARGS=PARSER.parse_args()
PG=os.environ.get('DEROYQUE_PSQL','psql')
HOST=os.environ.get('DEROYQUE_PG_HOST','127.0.0.1')
PGPORT=os.environ.get('DEROYQUE_PG_PORT','55432')
DB=os.environ.get('DEROYQUE_PG_DB','postgres')
USER=os.environ.get('DEROYQUE_PG_USER','nobody')
PASS=os.environ.get('DEROYQUE_PG_PASSWORD','')
PGJAR=os.environ.get('POSTGRES_JAR','/mnt/data/postgresql-42.7.14.jar')
AMPJAR=os.environ.get('RABBITMQ_CLIENT_JAR','/mnt/data/amqp-client-5.37.0.jar')
TAG=uuid.uuid4().hex[:11]
SCHEMA='m12_smoke_'+TAG
QUEUE='m12.smoke.'+TAG
JDBC=f'jdbc:postgresql://{HOST}:{PGPORT}/{DB}?currentSchema={SCHEMA}'
READ=secrets.token_urlsafe(28);WRITE=secrets.token_urlsafe(28)
RESULT={'mode':ARGS.mode,'tests':[],'schema_prefix':'m12_smoke_','flyway_runtime_verified':False,'engine_completed':False}
CHILDREN=[]
TMP=tempfile.TemporaryDirectory(prefix='deroyque-m12-smoke-');TMPPATH=pathlib.Path(TMP.name)

def must(condition,message):
    RESULT['tests'].append({'name':message,'passed':bool(condition)})
    print(('PASS' if condition else 'FAIL')+' '+str(len(RESULT['tests']))+'/'+str(len(RESULT['tests']))+' '+message,flush=True)
    if not condition:raise AssertionError(message)

def sql(statement):
    env={**os.environ,'PGPASSWORD':PASS}
    p=subprocess.run([PG,'-X','-A','-t','-v','ON_ERROR_STOP=1','-h',HOST,'-p',PGPORT,'-U',USER,'-d',DB,'-c',statement],
      env=env,text=True,capture_output=True,timeout=15)
    if p.returncode:raise RuntimeError('PostgreSQL command failed: '+p.stderr[-800:])
    return p.stdout.strip()

def freeport():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

API=freeport();PROVIDER=freeport()

def start(name,args,env):
    log=(TMPPATH/(name+'.log')).open('w')
    p=subprocess.Popen(args,env={**os.environ,**env},stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    CHILDREN.append((p,log))
    return p

def request(method,path,payload=None,token=None,ctype='application/json',timeout=5):
    headers={}
    if token is not None:headers['Authorization']='Bearer '+token
    data=payload
    if isinstance(payload,dict):data=json.dumps(payload,separators=(',',':')).encode()
    if data is not None:headers['Content-Type']=ctype
    req=urllib.request.Request(f'http://127.0.0.1:{API}'+path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:return r.status,r.read().decode(),dict(r.headers)
    except urllib.error.HTTPError as ex:return ex.code,ex.read().decode(),dict(ex.headers)

def eventually(predicate,seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            v=predicate()
            if v:return v
        except (OSError,ValueError,KeyError,urllib.error.URLError):pass
        time.sleep(.12)
    return None

def stage(rid):
    status,body,_=request('GET','/api/v1/requests/'+rid,token=READ)
    return json.loads(body).get('status') if status==200 else None

def provider_effects():
    with sqlite3.connect(TMPPATH/'provider.sqlite3') as c:
        return c.execute('select count(*) from effect').fetchone()[0]

def adversarial_jvm_budget(max_jvms):
    """Reserve RAM for two Spring JVMs and dependencies, then bound producer JVMs.

    This is a preflight memory estimate, not proof that actual RSS will fit.
    Never override the simulator's independent cgroup guard.
    """
    if not 8 <= max_jvms <= 256:
        raise ValueError('adversarial maximum JVMs must be 8..256')
    try:
        cap=pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip()
        used=int(pathlib.Path('/sys/fs/cgroup/memory.current').read_text().strip())
        available=int(cap)-used if cap!='max' else None
    except (ValueError,OSError):available=None
    if available is None:
        with open('/proc/meminfo') as f:
            found=[int(line.split()[1])*1024 for line in f if line.startswith('MemAvailable:')]
        available=found[0] if found else 0
    reserved=1024*1024*1024
    per_producer=(64+48)*1024*1024
    budget=max(0,int((available-reserved)*0.65))//per_producer
    count=min(max_jvms,budget)
    if count<8:raise RuntimeError(f'Insufficient headroom for 8 adversarial JVMs; budget={count}, available_MiB={available//1048576}')
    return count

def simulator_diagnostics(folder,return_code):
    """Report non-secret counters only, never dump raw provider logs or tokens."""
    path=folder/'summary.json'
    if not path.exists():
        agent_logs=list(folder.glob('agent-*.log')) if folder.exists() else []
        print('SIMULATOR_DIAG exit='+str(return_code)+' summary=missing agent_logs='+str(len(agent_logs)),flush=True)
        return
    try:
        data=json.loads(path.read_text())
        observed=data.get('observed') or {}
        print('SIMULATOR_DIAG '+json.dumps({
          'exit':return_code,'outcome':data.get('outcome'),
          'records':observed.get('records'),
          'http_statuses':observed.get('http_statuses'),
          'unexpected':observed.get('unexpected_http_or_transport'),
          'peak_jvms_with_inflight_http':observed.get('peak_jvms_with_inflight_http'),
          'verified_states':observed.get('verified_states'),
          'violations_count':len(data.get('violations') or []),
          'harness_errors_count':len(data.get('harness_errors') or []),
          'state_verification_errors_count':len(data.get('state_verification_errors') or [])
        },sort_keys=True),flush=True)
    except (OSError,ValueError,TypeError):
        print('SIMULATOR_DIAG exit='+str(return_code)+' summary=invalid',flush=True)

def wait_for_admission(prefix,minimum,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        admitted=int(sql("SELECT count(*) FROM "+SCHEMA+".p9_requests WHERE idem_key LIKE '"+prefix+"-%'"))
        if admitted>=minimum:return admitted
        time.sleep(.3)
    return 0

def adversarial_spring_test(jar,base_env):
    if ARGS.mode!='boot':raise RuntimeError('Multi-JVM Spring stress requires REAL Spring Boot')
    jvms=adversarial_jvm_budget(ARGS.adversarial_max_jvms)
    peer_port=freeport()
    peer=start('spring-peer',['java','-Xmx384m','-jar',str(jar)],{
        **base_env,'SERVER_PORT':str(peer_port),'P13_API_PORT':str(peer_port),
        'APP_LAB_METRICS_ENABLED':'0'})
    def peer_ready():
        if peer.poll() is not None:raise RuntimeError('Independent Spring worker JVM exited during boot')
        with urllib.request.urlopen(f'http://127.0.0.1:{peer_port}/api/v1/health/readiness',timeout=2) as r:
            return r.status==200
    must(bool(eventually(peer_ready,45)),'second independent Spring Boot JVM is ready against SAME PostgreSQL and RabbitMQ')
    must(peer.pid!=CHILDREN[1][0].pid,'Spring peers have distinct process IDs')
    run_id='adv-'+TAG
    simdir=TMPPATH/'adversarial-load'
    args=[sys.executable,'-u',str(ROOT/'simulator/run.py'),
          '--url',f'http://127.0.0.1:{API}','--jvms',str(jvms),
          '--concurrency','8','--requests','960','--keys','160',
          '--conflict-percent','40','--rate','110','--start-delay-ms','6500',
          '--timeout-ms','10000','--await-done-seconds','100',
          '--heap-max-mb','64','--active-processors','1',
          '--run-id',run_id,'--verify-state','--output-dir',str(simdir)]
    sim=start('adversarial-generator',args,{
        'DEROYQUE_SIM_WRITE_TOKEN':WRITE,'DEROYQUE_SIM_READ_TOKEN':READ})
    admitted=wait_for_admission(run_id,12,45)
    must(admitted>=12 and sim.poll() is None,'independent producers admitted work while both Spring JVMs were alive')
    must(peer.poll() is None,'peer Spring JVM remained alive until deliberate crash injection')
    os.killpg(peer.pid,signal.SIGKILL)
    peer.wait(timeout=8)
    must(peer.returncode==-signal.SIGKILL,'second Spring JVM was killed by SIGKILL, not graceful shutdown')
    must(CHILDREN[1][0].poll() is None,'surviving Spring JVM remained alive after peer crash')
    try:sim.wait(timeout=160)
    except subprocess.TimeoutExpired:raise RuntimeError('adversarial load generator exceeded 160s')
    if sim.returncode!=0:simulator_diagnostics(simdir,sim.returncode)
    must(sim.returncode==0,'multi-JVM simulator finished and validated every generated request')
    report=json.loads((simdir/'summary.json').read_text()) if (simdir/'summary.json').exists() else {}
    observed=report.get('observed',{})
    peak=observed.get('peak_jvms_with_inflight_http',0)
    must(observed.get('records')==960 and observed.get('admitted_keys_observed')==160,
         '960 POSTs and 160 unique keys observed across independent producer JVMs')
    must(peak>=8,'at least eight PRODUCER JVMs actually overlapped in flight (not merely launched)')
    must(report.get('outcome')=='PASS' and not report.get('violations') and not report.get('state_verification_errors'),
         'global idempotency and all final request states passed after worker crash')
    with sqlite3.connect(TMPPATH/'provider.sqlite3') as c:
        effects=c.execute('SELECT count(*) FROM effect WHERE key LIKE ?',(run_id+'-%',)).fetchone()[0]
    must(effects==160,'provider SQLite recorded exactly 160 external effects for 160 keys (no duplicates)')
    RESULT['adversarial']={'producer_jvms_configured':jvms,
        'producer_jvms_with_overlapping_http':peak,
        'requests':960,'keys':160,'effects':effects,'peer_killed_with_sigkill':True,
        'producer_throughput_qps':observed.get('post_attempts_per_second'),
        'outcome':report.get('outcome')}
    print('ADVERSARIAL_OUTCOME PASS jvms='+str(jvms)+' overlap='+str(peak)+
          ' HTTP=960 keys=160 effects='+str(effects),flush=True)

def run():
    jar=pathlib.Path(ARGS.boot_jar).resolve()
    if ARGS.mode=='boot' and not jar.is_file():
        print('BLOCKED: real Spring Boot executable JAR not found; compile with Maven in a networked environment. No stand-ins accepted.',flush=True)
        return 3
    if ARGS.mode=='legacy':
        if not (ROOT/'build/dist/runtime-rabbit.jar').exists():subprocess.run([str(ROOT/'scripts/build.sh')],check=True)
        cp=subprocess.check_output([str(ROOT/'scripts/print-classpath.sh')],text=True).strip()
    else:cp=None
    sql('CREATE SCHEMA '+SCHEMA)
    if ARGS.mode=='legacy':
        init=subprocess.run(['java','-cp',cp,'demo.JdbcRabbitLedger','org.postgresql.Driver',JDBC,USER,PASS,'schema'],
          text=True,capture_output=True,timeout=20)
        must(init.returncode==0 and 'SCHEMA_CREATED' in init.stdout,'legacy mode alone creates schema via SQL helper')
    env={
      'P13_JDBC':JDBC,'P13_DB_USER':USER,'P13_DB_PASSWORD':PASS,'P13_QUEUE':QUEUE,
      'P13_API_PORT':str(API),'SERVER_PORT':str(API),'SERVER_ADDRESS':'127.0.0.1',
      'SPRING_DATASOURCE_URL':JDBC,'SPRING_DATASOURCE_USERNAME':USER,'SPRING_DATASOURCE_PASSWORD':PASS,
      'SPRING_FLYWAY_SCHEMAS':SCHEMA,'SPRING_FLYWAY_DEFAULT_SCHEMA':SCHEMA,
      'P13_PROVIDER_URL':f'http://127.0.0.1:{PROVIDER}/activate',
      'P19_RECONCILE_URL':f'http://127.0.0.1:{PROVIDER}/lookup',
      'P16_PROVIDER_RETENTION_MS':'0','P20_SIGNED_LOOKUP_REQUIRED':'1',
      'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
      'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
      'APP_API_ENABLED':'1','APP_LAB_METRICS_ENABLED':'0','APP_READ_TOKEN':READ,'APP_WRITE_TOKEN':WRITE,
    }
    provider_env={
       'P19_PROVIDER_SQLITE':str(TMPPATH/'provider.sqlite3'),'P19_PROVIDER_PORT':str(PROVIDER),
       'P19_PROVIDER_RETENTION_MS':'60000','P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
       'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01'
    }
    prov=start('provider',['python3','-u',str(ROOT/'provider/independent_provider.py')],provider_env)
    def provider_ready():
        if prov.poll() is not None:raise RuntimeError('Provider exited unexpectedly')
        with urllib.request.urlopen(f'http://127.0.0.1:{PROVIDER}/health',timeout=2) as r:return r.status==200
    must(bool(eventually(provider_ready,10)),'independent SQLite provider is running')
    cmd=['java','-jar',str(jar)] if ARGS.mode=='boot' else ['java','-cp',cp,'demo.DeroyqueApplication']
    app=start('service',cmd,env)
    def ready():
        if app.poll() is not None:raise RuntimeError('Service exited unexpectedly; see private execution logs')
        return request('GET','/api/v1/health/readiness',timeout=2)[0]==200
    must(bool(eventually(ready,35)),'application readiness reports PostgreSQL and broker available')
    if ARGS.mode=='boot':
        rows=sql('SELECT installed_rank,version,success FROM '+SCHEMA+'.flyway_schema_history ORDER BY installed_rank')
        history=[line.split('|') for line in rows.splitlines() if line.strip()]
        must(len(history)>=2 and history[0][1]=='1' and history[1][1]=='2' and all(r[2]=='t' for r in history),'real Flyway applied and recorded successful V1 and V2 on an initially empty schema')
        RESULT['flyway_runtime_verified']=True
    else:must(sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='"+SCHEMA+"' AND table_name='flyway_schema_history'")=='0','legacy control has no Flyway history (not a Spring validation)')
    must(request('GET','/api/v1/health/liveness')[0]==200,'liveness endpoint')
    must(request('GET','/api/v1/requests/unknown')[0]==401,'anonymous status forbidden')
    must(request('POST','/api/v1/requests',{},READ)[0]==401,'read token cannot create')
    must(request('GET','/api/v1/requests/unknown',token=WRITE)[0]==401,'write token cannot read')
    must(request('GET','/api/v1/lab/ingress-metrics?run=test',token=READ)[0]==404,'instrumentation disabled by default')
    must(request('POST','/api/v1/requests',b'{}',WRITE,'text/plain')[0]==415,'invalid media type rejected')
    must(request('POST','/api/v1/requests',b'A'*4100,WRITE)[0]==413,'body limit enforced')
    must(request('POST','/api/v1/requests',b'{bad}',WRITE)[0]==400,'malformed JSON rejected')
    must(request('POST','/api/v1/requests',b'{"externalId":"x","externalId":"y"}',WRITE)[0]==400,'duplicate JSON keys rejected')
    unique='m12-order-'+TAG
    payload={'externalId':unique,'customerId':'customer-abc','productCode':'basic'}
    code,body,headers=request('POST','/api/v1/requests',payload,WRITE)
    created=json.loads(body) if code==202 else {}
    rid=created.get('id')
    must(code==202 and isinstance(rid,str) and len(rid)==36,'first admission is HTTP 202 with durable UUID')
    must(headers.get('Location')=='/api/v1/requests/'+rid,'Location points to persisted request')
    code,body,_=request('POST','/api/v1/requests',payload,WRITE)
    replay=json.loads(body) if code==200 else {}
    must(code==200 and replay.get('id')==rid,'idempotent repeat returns same UUID and HTTP 200')
    must(request('POST','/api/v1/requests',{**payload,'productCode':'different'},WRITE)[0]==409,'same identity with conflicting payload is HTTP 409')
    must(bool(eventually(lambda:stage(rid)=='DONE',25)),'RabbitMQ worker completes activation using signed provider evidence')
    must(provider_effects()==1,'independent SQLite provider persisted exactly one external effect for admitted identity')
    must(sql("SELECT count(*) FROM "+SCHEMA+".p20_signed_attestation")=='1','signed evidence stored transactionally')
    RESULT['engine_completed']=True
    if ARGS.load_jvms>0:
        # Reuse the FIRST-CLASS input simulator, not an ad-hoc thread pool.
        subprocess.run([str(ROOT/'simulator/build.sh')],check=True,stdout=subprocess.DEVNULL,timeout=45)
        simdir=TMPPATH/'load'
        args=[sys.executable,str(ROOT/'simulator/run.py'),
          '--url',f'http://127.0.0.1:{API}','--jvms',str(ARGS.load_jvms),
          '--concurrency','6','--requests','160','--keys','40',
          '--conflict-percent','35','--rate','55','--start-delay-ms','2500',
          '--timeout-ms','10000','--await-done-seconds','75','--verify-state',
          '--output-dir',str(simdir)]
        simenv={**os.environ,'DEROYQUE_SIM_WRITE_TOKEN':WRITE,'DEROYQUE_SIM_READ_TOKEN':READ}
        cp=subprocess.run(args,env=simenv,capture_output=True,text=True,timeout=120)
        if cp.returncode:
            # Avoid printing logs with credentials or user-supplied payloads.
            print('SIMULATOR FAILED; sanitized counters follow (no raw logs).',flush=True)
            simulator_diagnostics(simdir,cp.returncode)
        must(cp.returncode==0,'independent JVM input simulator exited successfully')
        summary=json.loads((simdir/'summary.json').read_text())
        observed=summary.get('observed',{})
        must(observed.get('records')==160,'simulator recorded 160 HTTP admissions')
        must(observed.get('admitted_keys_observed')==40,'simulator observed 40 independent idempotency keys')
        must(not summary.get('violations') and not summary.get('state_verification_errors'),'simulator global idempotency and state audits have no violations')
        must(summary.get('outcome')=='PASS','simulator reports PASS including final states')
        RESULT['simulator']={'jvms_configured':ARGS.load_jvms,'requests':160,
                             'keys':40,'outcome':summary.get('outcome')}
    if ARGS.adversarial_max_jvms:
        adversarial_spring_test(jar,env)
    return 0

def main():
    rc=1
    try:rc=run()
    except Exception as exc:
        RESULT['error_type']=type(exc).__name__
        print('ERROR '+type(exc).__name__+': '+str(exc)[:300],flush=True)
    finally:
        for proc,log in CHILDREN[::-1]:
            if proc.poll() is None:
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    proc.wait()
            log.close()
        failed=sum(not t['passed'] for t in RESULT['tests'])
        RESULT.update({'failed':failed,'passed':sum(t['passed'] for t in RESULT['tests']),'total':len(RESULT['tests']),'exit_code':rc})
        print('RESULT FAILURES {}/{} PASSED {}/{} MODE {}'.format(failed,len(RESULT['tests']),len(RESULT['tests'])-failed,len(RESULT['tests']),ARGS.mode),flush=True)
        if ARGS.output:
            out=pathlib.Path(ARGS.output);out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(RESULT,indent=2)+'\n')
        TMP.cleanup()
    return rc

if __name__=='__main__':sys.exit(main())
