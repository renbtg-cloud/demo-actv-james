"""Negative-control tests for the GitHub Actions evidence gate."""
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from ci_evidence_gate import SCENARIOS, validate, no_duplicate_json_pairs


def report(scenario):
    minimum=SCENARIOS[scenario][1]
    d={'total':minimum,'passed':minimum,'failed':0,
       ('tests' if scenario=='staggered-crashes' else 'checks'):
           [{'name':'safety check '+str(i),'passed':True} for i in range(minimum)],
       'exit' if scenario in ('competing-windows','dependency-interruptions','late-and-contradiction') else 'exit_code':0}
    if scenario=='staggered-crashes':d.update(mode='boot',flyway_runtime_verified=True,engine_completed=True,adversarial={'spring_service_jvms_started':4})
    elif scenario=='jwt-security':d['signed_effect_verified']=True
    elif scenario in ('oidc-keycloak','local-keycloak'):d.update(issuer_verified=True,signed_provider_effect_verified=True)
    elif scenario=='gateway-witness':d.update(completed=True,business_effects=20,durable_requests=20,signed_attestations=20, measurement={'global_recorded_posts':80,'global_throughput_qps':50.0,'gateway_witness':{'total_http_responses':80,'backend_count':2},'verified_states':{'DONE':20}})
    elif scenario=='same-row-contention':d.update(completed=True,business_effects=1)
    else:d['cases']=[{'case':'real'}]
    return d


class EvidenceGateTest(unittest.TestCase):
    def test_positive_all_scenarios(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):self.assertEqual(validate(scenario,report(scenario))['failed'],0)

    def test_empty_assertions(self):
        doc=report('jwt-security');doc.update(total=0,passed=0,checks=[])
        with self.assertRaises(ValueError):validate('jwt-security',doc)

    def test_claimed_total_not_matching_detail(self):
        doc=report('jwt-security');doc['total']+=1;doc['passed']+=1
        with self.assertRaises(ValueError):validate('jwt-security',doc)

    def test_lying_counter(self):
        doc=report('jwt-security');doc['checks'][2]['passed']=False
        with self.assertRaises(ValueError):validate('jwt-security',doc)

    def test_missing_external_effect(self):
        doc=report('jwt-security');doc['signed_effect_verified']=False
        with self.assertRaises(ValueError):validate('jwt-security',doc)

    def test_missing_keycloak_issuer(self):
        doc=report('oidc-keycloak');doc['issuer_verified']=False
        with self.assertRaises(ValueError):validate('oidc-keycloak',doc)

    def test_wrong_runtime_for_staggered(self):
        doc=report('staggered-crashes');doc['mode']='legacy'
        with self.assertRaises(ValueError):validate('staggered-crashes',doc)

    def test_wrong_business_effect_count(self):
        doc=report('same-row-contention');doc['business_effects']=2
        with self.assertRaises(ValueError):validate('same-row-contention',doc)

    def test_nonzero_exit(self):
        doc=report('competing-windows');doc['exit']=1
        with self.assertRaises(ValueError):validate('competing-windows',doc)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(ValueError):no_duplicate_json_pairs([('total',8),('total',0)])

    def test_false_gateway_witness_claim_fails_closed(self):
        data=report('gateway-witness')
        data['measurement']['gateway_witness']['backend_count']=1
        with self.assertRaises(ValueError):validate('gateway-witness',data)

    def test_gateway_signed_evidence_is_mandatory(self):
        data=report('gateway-witness')
        data['signed_attestations']=19
        with self.assertRaises(ValueError):validate('gateway-witness',data)

    def test_boolean_not_integer(self):
        doc=report('jwt-security');doc['total']=True
        with self.assertRaises(ValueError):validate('jwt-security',doc)


if __name__=='__main__':unittest.main()
