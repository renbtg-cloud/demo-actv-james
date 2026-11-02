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
    elif scenario in ('gateway-witness','gateway-soak'):
        soak=scenario=='gateway-soak';n=240 if soak else 80;k=60 if soak else 20
        d.update(completed=True,profile='soak' if soak else 'short',workload_duration_seconds=120 if soak else 0,business_effects=k,durable_requests=k,signed_attestations=k,identity_graph_verified=True,provider_per_key_verified=True,independent_ed25519_verified=True, terminal_signed_lineage_verified=True, uncontaminated_schema_verified=True, provider_index_and_time_verified=True,payload_contract_verified=True, measurement={'global_recorded_posts':n,'global_throughput_qps':50.0,'gateway_witness':{'total_http_responses':n,'backend_count':2},'verified_states':{'DONE':k}})
    elif scenario=='gateway-trials':d.update(completed=True,comparison={'trial_count':3,'backend_count':2,'host_provenance_status':'SAME_KERNEL_OBSERVED','median_gateway_post_qps':30.0,'median_absolute_deviation':1.0,'workload_sha256':'a'*64})
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

    def test_gateway_per_identity_proof_is_mandatory(self):
        for scenario in ('gateway-witness','gateway-soak'):
            for flag in ('identity_graph_verified','provider_per_key_verified','independent_ed25519_verified',
                         'terminal_signed_lineage_verified','uncontaminated_schema_verified','provider_index_and_time_verified','payload_contract_verified'):
                with self.subTest(scenario=scenario,flag=flag):
                    data=report(scenario);data[flag]=False
                    with self.assertRaises(ValueError):validate(scenario,data)

    def test_soak_proof_is_not_the_short_run(self):
        data=report('gateway-soak')
        data['measurement']['global_recorded_posts']=80
        with self.assertRaises(ValueError):validate('gateway-soak',data)

    def test_three_trial_rate_cannot_be_fabricated(self):
        data=report('gateway-trials')
        data['comparison']['median_gateway_post_qps']=None
        with self.assertRaises(ValueError):validate('gateway-trials',data)

    def test_boolean_not_integer(self):
        doc=report('jwt-security');doc['total']=True
        with self.assertRaises(ValueError):validate('jwt-security',doc)


if __name__=='__main__':unittest.main()
