#!/usr/bin/env python3
"""Two real independent Java JVMs competing to commit the exact same attestation OID."""
import pathlib,uuid,subprocess,urllib.parse,time
import run_p20 as t
import run_p19 as old
ROOT=pathlib.Path(__file__).resolve().parent
COUNTS={'passed':0,'failed':0}
def check(v,s):
 COUNTS['passed' if v else 'failed']+=1
 print(('PASS ' if v else 'FAIL ')+str(COUNTS['passed' if v else 'failed'])+'/'+str(sum(COUNTS.values()))+' '+s,flush=True)
 if not v:raise AssertionError(s)

def observe(s,oid,did,status,nonce,proof_file):
 return ['java','-cp',old.CP,'demo.P20Observe',s.jdbc,str(t.PUBLIC),'deroyque-lab-provider','lab-key-01',oid,did,str(status),nonce,str(proof_file)]

def run():
 s=t.Scenario('signed-jvm-race')
 try:
  s.provider();s.service();rid,key=s.submit('after500')
  check(old.until(lambda:s.stage(rid)=='DONE',10),'initial valid proof adopted')
  did=old.sql(f"SELECT did FROM {s.schema}.p9_dispatch WHERE rid='{rid}' LIMIT 1")
  check(bool(did),'durable dispatch found')
  nonce=uuid.uuid4().hex
  st,_,headers=old.req(old.PROVIDER_PORT,'/lookup?key='+urllib.parse.quote(key)+'&nonce='+nonce)
  proof=headers.get('X-Provider-Attestation') or headers.get('X-provider-attestation')
  check(st==200 and proof and len(proof)>100,'fresh signed evidence captured')
  f=ROOT/'logs'/'p20-race-attestation.txt';f.write_text(proof)
  oid=str(uuid.uuid4());a=observe(s,oid,did,200,nonce,f)
  p1=subprocess.Popen(a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  p2=subprocess.Popen(a,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  outs=[p1.communicate(timeout=12),p2.communicate(timeout=12)]
  statuses=sorted(out[0].strip().split()[-1] for out in outs)
  check(p1.returncode==p2.returncode==0,'both JVMs completed without SQL error')
  check(statuses==['ALREADY','COMMITTED'],'only one COMMIT, other idempotent replay')
  check(old.sql(f"SELECT count(*) FROM {s.schema}.p20_signed_attestation WHERE oid='{oid}'")=='1','one signed attestation persisted for shared oid')
  check(old.sql(f"SELECT count(*) FROM {s.schema}.p9_observation WHERE oid='{oid}'")=='1','one matching observation persisted atomically')
  nonce2=uuid.uuid4().hex
  st2,_,hdr2=old.req(old.PROVIDER_PORT,'/lookup?key='+urllib.parse.quote(key)+'&nonce='+nonce2)
  f2=ROOT/'logs'/'p20-race-attestation-conflict.txt';f2.write_text(hdr2['X-Provider-Attestation'])
  bad=subprocess.run(observe(s,oid,did,st2,nonce2,f2),capture_output=True,text=True,timeout=12)
  check(bad.returncode==0 and 'CONFLICT' in bad.stdout,'new valid signed observation cannot repurpose existing oid')
  check(old.sql(f"SELECT count(*) FROM {s.schema}.p20_signed_attestation WHERE oid='{oid}'")=='1','conflict did not replace original signed bytes')
  wrong=subprocess.run(observe(s,str(uuid.uuid4()),did,st2,'unknown-nonce',f2),capture_output=True,text=True,timeout=12)
  check(wrong.returncode!=0,'valid signature bound to wrong challenge is rejected by ledger')
  check(old.sql(f"SELECT count(*) FROM {s.schema}.p20_signed_attestation a JOIN {s.schema}.p9_dispatch d USING(did) WHERE d.rid='{rid}'")=='2','no extra signed row from challenge mismatch')
  check(s.effects(key)==1,'independent provider effect unchanged by concurrent proof processing')
 finally:s.close()
if __name__=='__main__':
 try:run();print(f'P20-RACES SUMMARY FAILURES {COUNTS["failed"]}/{sum(COUNTS.values())}; PASSED {COUNTS["passed"]}/{sum(COUNTS.values())}')
 except BaseException as e:print(f'P20-RACES SUMMARY FAILURES {COUNTS["failed"]}/{sum(COUNTS.values())}; PASSED {COUNTS["passed"]}/{sum(COUNTS.values())}; ERROR {e!r}');raise
