#!/usr/bin/env python3
"""Real Spring Security 7 resource-server integration with disposable RSA/JWKS.

The issuer and public JWK endpoint are locally generated for CI; there are no
static signed tokens or private production credentials. Exercise HTTP security
and a real PostgreSQL/RabbitMQ/independent SQLite provider activation.
"""
import argparse, base64, json, os, pathlib, secrets, signal, socket, sqlite3, subprocess, sys
import tempfile, time, urllib.error, urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor
from jwks_rotation_fixture import RotatingJwksFixture, b64

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

# A real local HTTP JWKS server; the same signer and endpoint are reused while
# publishing a second key and simulating an unavailable issuer.
jwks_fixture=RotatingJwksFixture(tag)
first_kid=jwks_fixture.add_key('initial')
issuer=jwks_fixture.uri.rsplit('/keys',1)[0]+'/issuer'
audience='deroyque-jwt-resource-'+tag

def token(scope='activation:write',kid=None,**overrides):
    return jwks_fixture.token(kid or first_kid, issuer, audience, scope, **overrides)

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
        'APP_JWT_JWKS_URI':jwks_fixture.uri,
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
    # Adversarial issuer lifecycle: introducing a previously unknown signing key
    # must cause a real JWKS refresh, while a key unavailable during issuer outage
    # must not be authenticated. This is NOT an instant revocation guarantee:
    # decoder caches can retain a retired public key until their TTL expires.
    fetches_before=jwks_fixture.get_count
    second_kid=jwks_fixture.add_key('rotated')
    second_read=token('activation:read',kid=second_kid)
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=second_read)[0]==404,
          'real JWT signed by newly published RS256 kid accepted without Spring restart')
    check(jwks_fixture.get_count>fetches_before,
          'Spring decoder fetched updated JWKS for newly introduced kid')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=real_read)[0]==404,
          'old signed key still accepted during deliberate overlap period')
    third_kid=jwks_fixture.add_key('during-outage',publish=False)
    third_read=token('activation:read',kid=third_kid)
    jwks_fixture.available(False)
    try:
        check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=third_read)[0]==401,
              'unpublished key denied while JWKS endpoint returns HTTP 503')
    finally:
        jwks_fixture.available(True)
    jwks_fixture.publish(third_kid)
    check(bool(eventually(lambda: http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=third_read)[0]==404,10)),
          'previously unavailable newly published JWKS key recovers without restart')
    jwks_fixture.retire(first_kid)
    # Intentionally make no claim that removing a key from JWKS immediately
    # invalidates old tokens: Nimbus may still cache the old public key.
    # Mixed-key reads only use currently published keys. Old tokens can be
    # accepted or rejected depending on cache age; neither outcome is a CI gate.
    retired_status=http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=real_read)[0]
    result['retired_key_immediate_http_status']=retired_status
    checks=[(second_read,404),(third_read,404)]*12
    with ThreadPoolExecutor(max_workers=12) as pool:
        actual=list(pool.map(lambda case: http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=case[0])[0],checks))
    check(len(actual)==len(checks) and all(actual[i]==expected for i,(_,expected) in enumerate(checks)),
          '24 concurrent mixed-kid reads preserve authentication and authorization')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('activation:read',kid=second_kid,iss='https://attacker.invalid'))[0]==401,
          'newly rotated key does not bypass issuer validation')
    check(http('GET','/api/v1/requests/'+str(uuid.uuid4()),auth=token('activation:read',kid=third_kid,aud=['attacker-api']))[0]==401,
          'post-outage key does not bypass audience validation')
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
        jwks_fixture.close();tmp.cleanup()
        passed=sum(x['passed'] for x in result['checks']);failed=len(result['checks'])-passed
        result.update({'failed':failed,'passed':passed,'total':len(result['checks']),'exit_code':code})
        print(f'JWT_SECURITY_RESULT FAILURES {failed}/{len(result["checks"])} PASSED {passed}/{len(result["checks"])}',flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,indent=2)+'\n')
    return code
if __name__=='__main__':sys.exit(main())
