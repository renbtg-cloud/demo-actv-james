#!/usr/bin/env python3
"""Real Spring Security 7 resource-server integration with disposable RSA/JWKS.

The issuer and public JWK endpoint are locally generated for CI; there are no
static signed tokens or private production credentials. Exercise HTTP security
and a real PostgreSQL/RabbitMQ/independent SQLite provider activation.
"""
import argparse, base64, json, os, pathlib, secrets, signal, socket, sqlite3, subprocess, sys
import tempfile, threading, time, urllib.error, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes

ROOT=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--boot-jar',type=pathlib.Path,required=True)
parser.add_argument('--output',type=pathlib.Path)
args=parser.parse_args()
pg={x:os.getenv('DEROYQUE_PG_'+x.upper(),default) for x,default in
    [('host','127.0.0.1'),('port','5432'),('db','deroyque_ci'),('user','deroyque_ci'),('password','')]}
psql=os.getenv('DEROYQUE_PSQL','psql')
tag=uuid.uuid4().hex[:10]
schema='jwt_'+tag;queue='jwt.'+tag;key='jwt-'+tag+'-activation'
result={'suite':'real-spring-oauth2-jwt','checks':[], 'signed_effect_verified':False}
tmp=tempfile.TemporaryDirectory(prefix='deroyque-jwt-');directory=pathlib.Path(tmp.name)
children=[]

def check(ok,description):
    n=len(result['checks'])+1;ok=bool(ok)
    result['checks'].append({'name':description,'passed':ok})
    print(f'{"PASS" if ok else "FAIL"} {n}/{n} {description}',flush=True)
    if not ok:raise AssertionError(description)

def sql(statement):
    p=subprocess.run([psql,'-X','-A','-t','-v','ON_ERROR_STOP=1','-h',pg['host'],'-p',pg['port'],
                      '-U',pg['user'],'-d',pg['db'],'-c',statement],
      env={**os.environ,'PGPASSWORD':pg['password']},capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError('PostgreSQL statement failed (exit metadata only)')
    return p.stdout.strip()

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def spawn(name,cmd,env):
    f=(directory/(name+'.log')).open('w')
    p=subprocess.Popen(cmd,env={**os.environ,**env},stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    children.append((p,f))
    return p

def eventually(callback,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=callback()
            if value:return value
        except (OSError,ValueError,urllib.error.URLError,TimeoutError):pass
        time.sleep(.16)
    return None

def b64(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')

# No fixed key material. A new signing key is generated for each CI run.
private_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
public=private_key.public_key().public_numbers()
kid='ci-jwt-'+tag
jwks={'keys':[{'kty':'RSA','alg':'RS256','use':'sig','kid':kid,
    'n':b64(public.n.to_bytes((public.n.bit_length()+7)//8,'big')),
    'e':b64(public.e.to_bytes((public.e.bit_length()+7)//8,'big'))}]}
class JwksHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path!='/keys':
            self.send_error(404);return
        b=json.dumps(jwks).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)))
        self.end_headers();self.wfile.write(b)
    def log_message(self,*unused):pass

jwks_server=ThreadingHTTPServer(('127.0.0.1',0),JwksHandler)
thread=threading.Thread(target=jwks_server.serve_forever,daemon=True);thread.start()
issuer=f'http://127.0.0.1:{jwks_server.server_port}/issuer'
audience='deroyque-jwt-resource-'+tag

def token(scope='activation:write',**overrides):
    now=int(time.time())
    claims={'iss':issuer,'aud':[audience],'sub':'ci-verified-user','iat':now,
            'nbf':now-10,'exp':now+600,'scope':scope}
    claims.update(overrides)
    header={'alg':'RS256','typ':'JWT','kid':kid}
    signing=b64(json.dumps(header,separators=(',',':')).encode())+'.'+b64(json.dumps(claims,separators=(',',':')).encode())
    signature=private_key.sign(signing.encode(),padding.PKCS1v15(),hashes.SHA256())
    return signing+'.'+b64(signature)

def http(method,path,auth=None,payload=None,timeout=5):
    headers={}
    if auth is not None:headers['Authorization']='Bearer '+auth
    body=json.dumps(payload,separators=(',',':')).encode() if payload is not None else None
    if body is not None:headers['Content-Type']='application/json'
    request=urllib.request.Request(f'http://127.0.0.1:{app_port}'+path,data=body,method=method,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=timeout) as r:
            return r.status,r.read().decode(),dict(r.headers)
    except urllib.error.HTTPError as e:return e.code,e.read().decode(),dict(e.headers)

def provider_effects():
    with sqlite3.connect(directory/'provider.sqlite3',timeout=10) as c:
        return c.execute('SELECT count(*) FROM effect WHERE key=?',(key,)).fetchone()[0]

def run():
    global app_port
    jar=args.boot_jar.resolve()
    check(jar.is_file(),'actual Spring Boot executable JAR exists')
    sql('CREATE SCHEMA '+schema)
    app_port=port();provider_port=port()
    jdbc=f'jdbc:postgresql://{pg["host"]}:{pg["port"]}/{pg["db"]}?currentSchema={schema}'
    jwt_env={
        'APP_AUTH_MODE':'jwt','APP_JWT_ISSUER':issuer,
        'APP_JWT_JWKS_URI':f'http://127.0.0.1:{jwks_server.server_port}/keys',
        'APP_JWT_AUDIENCE':audience,'APP_JWT_ALLOW_HTTP_LOOPBACK':'1',
        'APP_API_ENABLED':'1','APP_LAB_METRICS_ENABLED':'0',
        'P13_JDBC':jdbc,'P13_DB_USER':pg['user'],'P13_DB_PASSWORD':pg['password'],
        'SPRING_DATASOURCE_URL':jdbc,'SPRING_DATASOURCE_USERNAME':pg['user'],
        'SPRING_DATASOURCE_PASSWORD':pg['password'],
        'SPRING_FLYWAY_SCHEMAS':schema,'SPRING_FLYWAY_DEFAULT_SCHEMA':schema,
        'P13_QUEUE':queue,'P13_API_PORT':str(app_port),
        'SERVER_PORT':str(app_port),'SERVER_ADDRESS':'127.0.0.1',
        'P13_PROVIDER_URL':f'http://127.0.0.1:{provider_port}/activate',
        'P19_RECONCILE_URL':f'http://127.0.0.1:{provider_port}/lookup',
        'P16_PROVIDER_RETENTION_MS':'300000','P20_SIGNED_LOOKUP_REQUIRED':'1',
        'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
        'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
        # The deliberately obsolete static tokens must not be honored in JWT mode.
        'APP_READ_TOKEN':secrets.token_urlsafe(30),
        'APP_WRITE_TOKEN':secrets.token_urlsafe(30),
    }
    provider_env={'P19_PROVIDER_SQLITE':str(directory/'provider.sqlite3'),
        'P19_PROVIDER_PORT':str(provider_port),'P19_PROVIDER_RETENTION_MS':'600000',
        'P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
        'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01'}
    provider=spawn('provider',[sys.executable,'-u',str(ROOT/'provider/independent_provider.py')],provider_env)
    def ready_provider():
        if provider.poll() is not None:raise RuntimeError('provider startup failed')
        with urllib.request.urlopen(f'http://127.0.0.1:{provider_port}/health',timeout=2) as r:return r.status==200
    check(bool(eventually(ready_provider,12)),'independent SQLite provider is running')
    app=spawn('spring-jwt',['java','-Xmx384m','-jar',str(jar)],jwt_env)
    def ready_app():
        if app.poll() is not None:raise RuntimeError('JWT Spring application exited; private log retained locally')
        return http('GET','/api/v1/health/readiness',timeout=3)[0]==200
    check(bool(eventually(ready_app,40)),'Spring Boot JWT mode started and readiness permitted anonymously')
    flyway=sql('SELECT version,success FROM '+schema+'.flyway_schema_history ORDER BY installed_rank')
    check('1|t' in flyway and '2|t' in flyway,'Flyway V1/V2 executed in fresh JWT schema')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()))[0]==401,'no bearer token returns 401')
    check(http('POST','/api/v1/requests',payload={},auth=jwt_env['APP_WRITE_TOKEN'])[0]==401,
          'legacy laboratory bearer token rejected in JWT mode')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth='not.a.jwt')[0]==401,'malformed JWT rejected')
    real_read=token('activation:read');real_write=token('activation:write')
    bad_sig=real_read.rsplit('.',1)[0]+'.'+b64(secrets.token_bytes(256))
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=bad_sig)[0]==401,
          'invalid RSA signature rejected')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('activation:read',iss='https://wrong-issuer.invalid'))[0]==401,
          'wrong issuer rejected')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('activation:read',aud=['other-api']))[0]==401,
          'wrong audience rejected')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('activation:read',exp=int(time.time())-3600))[0]==401,
          'expired token rejected')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('activation:read',nbf=int(time.time())+3600))[0]==401,
          'not-yet-valid token rejected')
    check(http('POST','/api/v1/requests',auth=real_read,payload={})[0]==403,
          'valid read-scope JWT cannot create (403)')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=real_write)[0]==403,
          'valid write-scope JWT cannot read (403)')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('other:scope'))[0]==403,
          'valid JWT with irrelevant scope denied')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=real_read)[0]==404,
          'valid read-scope JWT reaches application lookup')
    check(http('GET','/api/v1/lab/ingress-metrics?run=test',auth=real_read)[0]==403,
          'lab-only metrics endpoint denied in JWT mode')
    payload={'externalId':key,'customerId':'customer-jwt-ci','productCode':'basic'}
    status,body,headers=http('POST','/api/v1/requests',auth=real_write,payload=payload)
    rid=json.loads(body).get('id') if status==202 else None
    check(status==202 and isinstance(rid,str) and len(rid)==36,
          'valid write-scope JWT creates durable request (202)')
    check(http('POST','/api/v1/requests',auth=real_write,payload=payload)[0]==200,
          'JWT idempotent replay returns HTTP 200')
    check(http('POST','/api/v1/requests',auth=real_write,payload={**payload,'productCode':'conflict'})[0]==409,
          'JWT idempotency conflict returns HTTP 409')
    def completed():
        status,body,_=http('GET','/api/v1/requests/'+rid,auth=real_read)
        return status==200 and json.loads(body).get('status')=='DONE'
    check(bool(eventually(completed,35)),'JWT read-scope sees completed signed activation')
    check(provider_effects()==1,'independent SQLite provider recorded exactly one external effect')
    check(sql('SELECT count(*) FROM '+schema+'.p20_signed_attestation')=='1',
          'signed evidence persisted in PostgreSQL')
    result['signed_effect_verified']=True
    return 0

def main():
    code=1
    try:code=run()
    except Exception as exc:
        result['error_type']=type(exc).__name__
        print('JWT_SECURITY_ERROR '+type(exc).__name__+' (sensitive local logs suppressed)',flush=True)
    finally:
        for p,f in children[::-1]:
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
            f.close()
        jwks_server.shutdown();jwks_server.server_close();tmp.cleanup()
        passed=sum(x['passed'] for x in result['checks']);failed=len(result['checks'])-passed
        result.update({'failed':failed,'passed':passed,'total':len(result['checks']),'exit_code':code})
        print(f'JWT_SECURITY_RESULT FAILURES {failed}/{len(result["checks"])} PASSED {passed}/{len(result["checks"])}',flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,indent=2)+'\n')
    return code
if __name__=='__main__':sys.exit(main())
