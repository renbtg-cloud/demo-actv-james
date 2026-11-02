#!/usr/bin/env python3
"""Live integration of Ed25519 signed, challenged, independently durable provider reports.
Uses only fresh lab PG schemas, quorum queues, and SQLite files. No destructive PG outage.
"""
import os,time,pathlib,subprocess,sqlite3,urllib.parse
import run_p19 as old
ROOT=pathlib.Path(__file__).resolve().parent
ROOTPATH=str(ROOT)
PUBLIC=ROOT/'keys/provider-test-public.pem'; PRIVATE=ROOT/'keys/provider-test-private.pem';ATTACKER=ROOT/'keys/attacker-test-private.pem'
COUNTS={'passed':0,'failed':0}
def check(condition,description):
 if condition:
  COUNTS['passed']+=1;print('PASS '+str(COUNTS['passed'])+'/'+str(sum(COUNTS.values()))+' '+description,flush=True)
 else:
  COUNTS['failed']+=1;print('FAIL '+str(COUNTS['failed'])+'/'+str(sum(COUNTS.values()))+' '+description,flush=True);raise AssertionError(description)
class Scenario(old.Scenario):
 def __init__(self,label,ttl=850):super().__init__(label,ttl=ttl)
 def provider(self,override='NONE',private=None):
  return self.spawn('provider',['python3','-u',str(ROOT/'provider_p20.py')],{
   'P19_PROVIDER_PORT':str(old.PROVIDER_PORT),'P19_PROVIDER_SQLITE':str(self.sqlite),
   'P19_PROVIDER_RETENTION_MS':str(self.ttl),'P20_PRIVATE_KEY_PEM':str(private or PRIVATE),
   'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01',
   'P20_LOOKUP_OVERRIDE':override},'P20_PROVIDER_READY')
 def service(self,extra=None):
  return super().service({'P20_SIGNED_LOOKUP_REQUIRED':'1','P20_PUBLIC_KEY_PEM':str(PUBLIC),
   'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',**(extra or {})})
 def attestations(self,rid):return int(old.sql(f"SELECT count(*) FROM {self.schema}.p20_signed_attestation a JOIN {self.schema}.p9_dispatch d USING(did) WHERE d.rid='{rid}'"))
 def invalid(self,rid):return int(old.sql(f"SELECT count(*) FROM {self.schema}.p20_rejected_attestation WHERE rid='{rid}'"))
 def reports(self,rid):return old.sql(f"SELECT string_agg(DISTINCT reported_outcome,',') FROM {self.schema}.p20_signed_attestation a JOIN {self.schema}.p9_dispatch d USING(did) WHERE d.rid='{rid}'")
 def stage(self,rid):return super().stage(rid)

def case_signed_positive(mode,label):
 s=Scenario(label)
 try:
  s.provider();s.service();check(s.health(),label+' service and independent provider online')
  rid,key=s.submit(mode)
  check(old.until(lambda:s.stage(rid)=='DONE',10),label+' signed lookup adopted ACTIVATED')
  check(s.attempts(rid)==1,label+' single authorized dispatch')
  check(s.effects(key)==1,label+' independent SQLite effect exactly one')
  check(s.attestations(rid)>=1,label+' signed report persisted in DB')
  check('SIGNED_PROVIDER_LOOKUP:ADOPT_ACTIVATED' in s.sources(rid),label+' signed provenance classified under pinned policy')
  check(int(old.sql(f"SELECT count(*) FROM {s.schema}.p20_signed_attestation a JOIN {s.schema}.p9_observation o USING(oid) WHERE a.token LIKE '%.%' AND o.action='ADOPT_ACTIVATED'"))>=1,label+' full signed proof committed with decisive observation')
  time.sleep(.95)
  check(s.effects(key)==1,label+' no second POST after retention expiry')
 finally:s.close()

def case_invalid(label,override,private=None,mode='after500'):
 s=Scenario(label)
 try:
  s.provider(override,private);s.service();rid,key=s.submit(mode)
  check(old.until(lambda:s.stage(rid)=='PAUSED',9),label+' invalid signed claim cannot settle success')
  check(old.until(lambda:s.invalid(rid)>=1,3),label+' rejected attestation durably journaled')
  check(s.attestations(rid)==0,label+' invalid proof did not enter verified attestation table')
  check(s.effects(key)==1,label+' provider independently executed one effect')
  check(s.attempts(rid)==1,label+' no automatic unsafe resend')
  s.stop('provider');s.provider('NONE')
  check(old.until(lambda:s.stage(rid)=='DONE',10),label+' recovered good proof permits later reconciliation')
  check(s.attestations(rid)>=1,label+' recovered valid proof persisted')
  check(s.effects(key)==1,label+' no second external effect during recovery')
 finally:s.close()

def case_signed_false_negative():
 s=Scenario('signed-false-negative')
 try:
  s.provider('ABSENT');s.service();rid,key=s.submit('after500')
  check(old.until(lambda:s.stage(rid)=='PAUSED',10),'signed false 404 cannot prove absence')
  check(s.effects(key)==1,'actual effect exists despite signed false negative')
  check('ABSENT' in s.reports(rid),'signed negative observation preserved')
  check('ADOPT_' not in s.sources(rid),'no signed negative converted to positive proof')
  s.stop('provider');s.provider()
  check(old.until(lambda:s.stage(rid)=='DONE',10),'later signed positive reconciles paused request')
  check(s.effects(key)==1,'no extra effect under false negative')
 finally:s.close()

def case_multiple():
 s=Scenario('signed-multiple',ttl=250)
 try:
  s.provider();rid='P20_'+old.RUN+'_signed_multiple';key='K_'+rid
  for n in range(2):
   status,_,_=old.req(old.PROVIDER_PORT,'/activate',{'key':key,'mode':'ok'})
   check(status==200,'seed external effect generation '+str(n+1))
   time.sleep(.37)
  check(s.effects(key)==2,'two distinct effects really exist')
  s.service()
  status,body,_=old.req(old.SVC_PORT,'/submit',{'rid':rid,'key':key,'mode':'before500'})
  check((status,body)==(202,'COMMITTED'),'request admitted after prior duplicate effects')
  check(old.until(lambda:s.stage(rid)=='PAUSED',10),'signed MULTIPLE does not resolve business success')
  check('MULTIPLE' in s.reports(rid),'authenticated multi-effect incident preserved')
  check(s.effects(key)==2,'no new effects from failing-before-effect POST')
 finally:s.close()

def case_terminal_contradiction():
 s=Scenario('signed-contradiction')
 try:
  s.provider();s.service();rid,key=s.submit('after500')
  check(old.until(lambda:s.stage(rid)=='DONE',10),'initial provider ACTIVATED attestation adopted')
  check(s.effects(key)==1,'initial real effect is ACTIVATED')
  s.stop('provider');s.provider('REJECTED')
  p=subprocess.run(['java','-cp',old.CP,'demo.P20Audit',s.jdbc,
    f'http://127.0.0.1:{old.PROVIDER_PORT}/lookup',str(PUBLIC),'deroyque-lab-provider',
    'lab-key-01',rid],capture_output=True,text=True,timeout=12)
  check(p.returncode==0 and 'adoption=CONFLICT' in p.stdout,'second contradictory signed report detected '+p.stdout.strip())
  check(s.stage(rid)=='DONE','terminal original result not silently overwritten')
  check('ACTIVATED' in s.reports(rid) and 'REJECTED' in s.reports(rid),'both opposite signed claims preserved')
  check(old.sql(f"SELECT kind FROM {s.schema}.p14_evidence_incident WHERE rid='{rid}'")=='CONTRADICTORY_PROOF','durable contradictory-proof incident recorded')
  check(s.effects(key)==1,'provider ledger still has only original effect; signed issuer may lie')
 finally:s.close()

def case_genuine_rejected():
 s=Scenario('genuine-rejected')
 try:
  s.provider();s.service();rid,key=s.submit('reject')
  check(old.until(lambda:s.stage(rid)=='REJECTED',10),'signed REJECTED (422) settled explicitly')
  check('SIGNED_PROVIDER_LOOKUP:ADOPT_REJECTED' in s.sources(rid),'signed rejection proof persisted')
  check(s.effects(key)==1,'durable rejection event counted at provider')
 finally:s.close()

def case_signed_issuer_lies():
 s=Scenario('signed-issuer-lies')
 try:
  s.provider('ACTIVATED');s.service();rid,key=s.submit('before500')
  check(old.until(lambda:s.stage(rid)=='DONE',10),'control: a lying but correctly signed issuer can trigger DONE')
  check(s.effects(key)==0,'control: provider effect ledger proves ZERO effects, despite signed success')
  check('ACTIVATED' in s.reports(rid),'control: signed assertion of activation retained for audit')
  check(s.attempts(rid)==1,'control: one dispatch, no actual external effect')
 finally:s.close()

def case_misconfigured_status_contract():
 label='status-only-negative-guard';s=Scenario(label)
 policy=ROOT/'policy-p20-dangerous-status.txt'
 policy.write_text('''STATUS_ONLY=YES
CONTRACT=The mock wrongly treats a negative lookup as definitive business rejection
200,ACTIVATED,ADOPT_ACTIVATED
422,REJECTED,ADOPT_REJECTED
404,ABSENT,CONTRACT_REJECTION
409,ABSENT,PAUSE_UNVERIFIED
''')
 try:
  s.provider('ABSENT');s.service({'P16_POLICY_FILE':str(policy)})
  rid,key=s.submit('before500')
  check(old.until(lambda:s.stage(rid)=='PAUSED',10),'signed ABSENT cannot become terminal via dangerous status-only policy')
  check(s.invalid(rid)>=1,'dangerous policy promotion rejected and journaled')
  check('ADOPT_' not in s.sources(rid) and 'CONTRACT_REJECTION' not in s.sources(rid),'no terminal proof produced')
  check(s.effects(key)==0,'mock confirms no effect, but signed absence still not a business rejection')
 finally:s.close()

if __name__=='__main__':
 try:
  case_signed_positive('after500','signed-after503')
  case_signed_positive('ok','unsigned-http-200')
  case_genuine_rejected()
  case_invalid('unsigned-lookup','UNSIGNED',mode='ok')
  case_invalid('bad-signature','BAD_SIGNATURE')
  case_invalid('wrong-key','WRONG_KEY')
  case_invalid('wrong-nonce','WRONG_NONCE')
  case_invalid('stale-snapshot','STALE')
  case_invalid('wrong-kid','WRONG_KID')
  case_invalid('wrong-signer','NONE',private=ATTACKER)
  case_signed_false_negative()
  case_multiple()
  case_terminal_contradiction()
  case_signed_issuer_lies()
  case_misconfigured_status_contract()
  print(f'P20 SUMMARY FAILURES {COUNTS["failed"]}/{sum(COUNTS.values())}; PASSED {COUNTS["passed"]}/{sum(COUNTS.values())}',flush=True)
 except BaseException as e:
  print(f'P20 SUMMARY FAILURES {COUNTS["failed"]}/{sum(COUNTS.values())}; PASSED {COUNTS["passed"]}/{sum(COUNTS.values())}; ERROR {e!r}',flush=True)
  raise
