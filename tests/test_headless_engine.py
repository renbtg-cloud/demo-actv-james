#!/usr/bin/env python3
"""Live PG/RabbitMQ/SQLite provider, clean Flyway-schema SQL, no HTTP server in the Java engine.
Destructive operations affect only a fresh m10_headless_* PostgreSQL schema and new queue.
"""
import os, pathlib, subprocess, tempfile, time, socket, uuid, sys, signal
R=pathlib.Path(__file__).resolve().parents[1]
SCHEMA='m10_headless_'+uuid.uuid4().hex[:10]
QUEUE='deroyque.m10.headless.'+uuid.uuid4().hex[:10]
PG='/mnt/data/infra-lab/pgsql/usr/lib/postgresql/17/bin/psql'
DSN='host=127.0.0.1 port=55432 dbname=postgres user=nobody'
with socket.socket() as x:
    x.bind(('127.0.0.1',0)); PROVPORT=x.getsockname()[1]
with socket.socket() as x:
    x.bind(('127.0.0.1',0)); APIPORT=x.getsockname()[1]
work=pathlib.Path(tempfile.mkdtemp(prefix='m10-headless-',dir='/mnt/data'))
sqlite=work/'provider.sqlite3';process=None

def psql(*args,dsn=DSN):
    subprocess.run([PG,dsn,'-X','-v','ON_ERROR_STOP=1',*args],check=True,stdout=subprocess.DEVNULL)
try:
    psql('-c',f'CREATE SCHEMA {SCHEMA}')
    for f in sorted((R/'adapter-springboot/src/main/resources/db/migration').glob('V*.sql')):
        psql('-f',str(f),dsn=DSN+' options=-csearch_path='+SCHEMA)
    # Real provider persists independently in SQLite, with signed receipts.
    env={**os.environ,'P19_PROVIDER_SQLITE':str(sqlite),'P19_PROVIDER_PORT':str(PROVPORT),
        'P19_PROVIDER_RETENTION_MS':'60000','P20_PRIVATE_KEY_PEM':str(R/'fixtures/keys/provider-test-private.pem'),
        'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01'}
    f=(work/'provider.log').open('w')
    process=subprocess.Popen(['python3','-u',str(R/'provider/independent_provider.py')],env=env,stdout=f,stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            with socket.create_connection(('127.0.0.1',PROVPORT),.2):break
        except OSError:time.sleep(.1)
    else:raise RuntimeError('provider startup failed')
    cp=subprocess.check_output([str(R/'scripts/print-classpath.sh')],text=True)
    classes=R/'build/test-headless';classes.mkdir(parents=True,exist_ok=True)
    subprocess.run(['javac','-cp',cp,'-d',str(classes),str(R/'tests/java/demo/HeadlessEngineSmoke.java')],check=True)
    env={**os.environ,'P13_JDBC':'jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema='+SCHEMA,
         'P13_QUEUE':QUEUE,'P13_API_PORT':str(APIPORT),
         'P13_PROVIDER_URL':f'http://127.0.0.1:{PROVPORT}/activate',
         'P19_RECONCILE_URL':f'http://127.0.0.1:{PROVPORT}/lookup',
         'P20_SIGNED_LOOKUP_REQUIRED':'1','P20_PUBLIC_KEY_PEM':str(R/'fixtures/keys/provider-test-public.pem'),
         'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
         'P16_PROVIDER_RETENTION_MS':'0'}
    result=subprocess.run(['java','-cp',str(classes)+':'+cp,'demo.HeadlessEngineSmoke'],env=env,text=True,capture_output=True,timeout=65)
    print(result.stdout,flush=True)
    if result.returncode: print('STDERR',result.stderr[-6000:],flush=True);raise RuntimeError('headless engine test failed')
    print('PROVIDER_SQLITE',sqlite,'POSTGRES_SCHEMA',SCHEMA,'PORT',PROVPORT)
finally:
    if process is not None:
        process.terminate()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
    # Keep the isolated schema for forensic inspection; never drop existing schemas.
