#!/usr/bin/env python3
"""Start an isolated local Deroyque demo. Expects *existing* PostgreSQL and RabbitMQ.
 Never shuts down or drops shared infrastructure. Ctrl-C stops only child processes.
"""
import os,pathlib,secrets,subprocess,time,uuid,json,signal
ROOT=pathlib.Path(__file__).resolve().parents[1]
PG=os.environ.get('DEROYQUE_PSQL','/mnt/data/infra-lab/pgsql/usr/lib/postgresql/17/bin/psql')
PGJAR=os.environ.get('POSTGRES_JAR','/mnt/data/postgresql-42.7.14.jar')
AMQPJAR=os.environ.get('RABBITMQ_CLIENT_JAR','/mnt/data/amqp-client-5.37.0.jar')
CP=f'{ROOT}/build/classes:{PGJAR}:{AMQPJAR}'
DB=os.environ.get('DEROYQUE_PG_DSN','host=127.0.0.1 port=55432 dbname=postgres user=nobody')
SCHEMA='deroyque_dev_'+uuid.uuid4().hex[:10]
JDBC=os.environ.get('DEROYQUE_JDBC_BASE','jdbc:postgresql://127.0.0.1:55432/postgres')+'?currentSchema='+SCHEMA
PORT=int(os.environ.get('DEROYQUE_API_PORT','18081'))
PROVIDER=int(os.environ.get('DEROYQUE_PROVIDER_PORT','18082'))
QUEUE='deroyque.dev.'+uuid.uuid4().hex[:10]
READ=secrets.token_urlsafe(32);WRITE=secrets.token_urlsafe(32)
LOCAL=ROOT/'.local';LOCAL.mkdir(exist_ok=True)
SQLITE=LOCAL/(SCHEMA+'.sqlite3')
CHILDREN=[]
def launch(cmd,env,log):
 placement={'service':'DEROYQUE_SERVICE_CPUS','provider':'DEROYQUE_PROVIDER_CPUS'}.get(log)
 if placement and os.environ.get(placement):
  # taskset preserves affinity for future Java virtual-thread carriers.
  # Does not reserve cores or isolate PostgreSQL/RabbitMQ from those cores.
  import sys
  sys.path.insert(0,str(ROOT/'simulator'))
  from cpu_placement import parse_cpus,format_cpus
  cpus=parse_cpus(os.environ[placement]);cmd=['taskset','-c',format_cpus(cpus),*cmd]
 f=(LOCAL/(SCHEMA+'-'+log+'.log')).open('w')
 p=subprocess.Popen(cmd,env={**os.environ,**env},stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
 CHILDREN.append((p,f))
 return p

def main():
 subprocess.run([str(ROOT/'scripts/build.sh')],check=True)
 pg=subprocess.run([PG,DB,'-X','-v','ON_ERROR_STOP=1','-c','CREATE SCHEMA '+SCHEMA],capture_output=True,text=True)
 if pg.returncode:raise RuntimeError(pg.stderr)
 pg=subprocess.run(['java','-cp',CP,'demo.JdbcRabbitLedger','org.postgresql.Driver',JDBC,'nobody','','schema'],capture_output=True,text=True)
 if pg.returncode:raise RuntimeError(pg.stderr)
 p=launch(['python3','-u',str(ROOT/'provider/independent_provider.py')],{
  'P19_PROVIDER_SQLITE':str(SQLITE),'P19_PROVIDER_PORT':str(PROVIDER),
  'P19_PROVIDER_RETENTION_MS':'60000',
  'P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
  'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01'},'provider')
 s=launch(['java','-cp',CP,'demo.DeroyqueApplication'],{
  'P13_JDBC':JDBC,'P13_QUEUE':QUEUE,'P13_API_PORT':str(PORT),
  'P13_PROVIDER_URL':f'http://127.0.0.1:{PROVIDER}/activate',
  'P19_RECONCILE_URL':f'http://127.0.0.1:{PROVIDER}/lookup',
  'P16_PROVIDER_RETENTION_MS':'0','P20_SIGNED_LOOKUP_REQUIRED':'1',
  'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
  'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
  'APP_API_ENABLED':'1','APP_LAB_METRICS_ENABLED':os.environ.get('APP_LAB_METRICS_ENABLED','0'),'APP_READ_TOKEN':READ,'APP_WRITE_TOKEN':WRITE},'service')
 config=LOCAL/(SCHEMA+'-credentials.txt')
 config.write_text('READ_TOKEN='+READ+'\nWRITE_TOKEN='+WRITE+'\nSCHEMA='+SCHEMA+'\nAPI_PORT='+str(PORT)+'\n')
 config.chmod(0o600)
 print('\nLocal laboratory started; will remain active until Ctrl-C.\n',flush=True)
 print('API: http://127.0.0.1:'+str(PORT),flush=True)
 print('Credential file (chmod 600):',config,flush=True)
 print('PostgreSQL schema:',SCHEMA,'\nSQLite:',SQLITE,flush=True)
 print('Ingress instrumentation:', 'ENABLED (laboratory only)' if os.environ.get('APP_LAB_METRICS_ENABLED','0')=='1' else 'DISABLED (default)',flush=True)
 print('Example POST:',flush=True)
 print("curl -sS -i -H 'Authorization: Bearer "+WRITE+"' -H 'Content-Type: application/json' -d "+
       "'"+json.dumps({'externalId':'sample-'+uuid.uuid4().hex[:9],'customerId':'customer-42','productCode':'basic'})+"' "+
       f'http://127.0.0.1:{PORT}/api/v1/requests',flush=True)
 print('Ctrl-C stops the service and provider, leaving schema/SQLite for inspection.\n',flush=True)
 while s.poll() is None and p.poll() is None:time.sleep(.3)
 raise RuntimeError('service or provider exited unexpectedly; inspect .local/*.log')

if __name__=='__main__':
 try:main()
 except KeyboardInterrupt:pass
 finally:
  for p,f in reversed(CHILDREN):
   if p.poll() is None:
    p.terminate()
    try:p.wait(timeout=3)
    except subprocess.TimeoutExpired:p.kill();p.wait()
   f.close()
