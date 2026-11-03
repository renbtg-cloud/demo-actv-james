#!/usr/bin/env python3
"""Independently revalidate saved signed preflight against post-run host identities.

A collector-held verdict is NOT proof that it was actually created before a
historical barrier; a dishonest collector can backdate it. Signed host reports
are self-reports, not hardware attestation or cross-host performance proof.
"""
import json
import pathlib
import hashlib
from host_matrix import _read_host_report_bytes
from host_matrix import audit
from host_readiness_signature import _read_regular, _strict_document
from host_session import validate, digest

_STATUS='ALL_PINNED_KEYS_VERIFIED_NOT_HOST_ATTESTATION'
_POSTRUN='ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION'
_TIMING='BOUNDED_SELF_REPORTED_TIMES_UNVERIFIED'


def reproduce_preflight(preflight_path, report_paths, proof_paths, manifest, pins, session):
    """Require a strict, fully signed preflight reproducible at its recorded time."""
    session_hash=validate(session,manifest,pins)
    stored=_strict_document(_read_regular(preflight_path,2*1024*1024)[0])
    if not isinstance(stored,dict) or stored.get('status')!='APPARENT_READY_DISTINCT_KERNEL_REPORTS_UNVERIFIED':
        raise ValueError('Preflight verdict missing apparent readiness')
    if stored.get('temporal_preflight')!=_TIMING:
        raise ValueError('Preflight must contain time-bounded evidence')
    checked=stored.get('collector_checked_at_epoch_ms')
    budgets=stored.get('temporal_budgets')
    if (type(checked) is not int or checked<=0 or not isinstance(budgets,dict) or
        set(budgets)!={'max_age_ms','max_future_ms'} or
        type(stored.get('network_required')) is not bool):
        raise ValueError('Preflight lacks exact collector policy and time anchor')
    saved_signed=stored.get('signed_readiness')
    if (not isinstance(saved_signed,dict) or saved_signed.get('status')!=_STATUS or
        saved_signed.get('session_sha256')!=session_hash):
        raise ValueError('Preflight lacks validated, session-bound signatures')
    reproduced=audit(manifest,report_paths,require_network=stored['network_required'],
                     strict_temporal=True,now_ms=checked,
                     max_age_ms=budgets['max_age_ms'],max_future_ms=budgets['max_future_ms'],
                     signed_readiness_paths=proof_paths,session=session,pins=pins)
    if reproduced!=stored:
        raise ValueError('Preflight verdict disagrees with independently reverified exact inputs')
    return stored


def verify_handoff(preflight_path, report_paths, proof_paths, manifest, pins, session, postrun, postrun_snapshots):
    """Only a complete, byte-for-byte independently reproducible verdict qualifies."""
    if not isinstance(postrun,dict) or postrun.get('status')!=_POSTRUN:
        raise ValueError('Post-run pinned signed host evidence must verify first')
    session_hash=validate(session,manifest,pins)
    stored=reproduce_preflight(preflight_path,report_paths,proof_paths,manifest,pins,session)
    saved_signed=stored['signed_readiness']
    signed_shards=saved_signed['verified_shards']
    postrun_shards=postrun.get('shards')
    n=manifest.get('shards')
    if (type(n) is not int or not isinstance(signed_shards,list) or
        not isinstance(postrun_shards,list) or len(signed_shards)!=n or
        len(postrun_shards)!=n or not isinstance(postrun_snapshots,list) or
        len(postrun_snapshots)!=n):
        raise ValueError('Incomplete preflight/postrun host inventory')
    report_fingerprints={}
    for p in report_paths:
        raw=_read_host_report_bytes(p)
        doc=_strict_document(raw)
        i=doc.get('shard_index') if isinstance(doc,dict) else None
        if (type(i) is not int or not 0<=i<n or i in report_fingerprints or
            hashlib.sha256(raw).hexdigest()!=signed_shards[i]['readiness_sha256']):
            raise ValueError('Readiness report changed since validated preflight')
        report_fingerprints[i]=doc['kernel_fingerprint']
    for i,(before,after) in enumerate(zip(signed_shards,postrun_shards)):
        if (not isinstance(before,dict) or not isinstance(after,dict) or
            type(before.get('shard_index')) is not int or before['shard_index']!=i or
            type(after.get('shard')) is not int or after['shard']!=i or
            before.get('host_id')!=session['host_assignments'][i] or
            after.get('host_id')!=before['host_id']):
            raise ValueError('Signed host or shard differs between preflight and post-run evidence')
        snapshot=postrun_snapshots[i]
        if (not isinstance(snapshot,dict) or snapshot.get('schema_version')!=1 or
            snapshot.get('kernel_fingerprint')!=report_fingerprints[i]):
            raise ValueError('Signed postrun kernel report differs from signed preflight')
    return {'status':'SIGNED_PREFLIGHT_AND_POSTRUN_IDENTITIES_AGREE_NOT_PHYSICAL_ATTESTATION',
            'run_id':manifest['run_id'],'session_sha256':session_hash,
            'preflight_verdict_sha256':digest(stored),
            'host_commitments':[{'shard_index':p['shard_index'],'host_id':p['host_id'],
                                 'readiness_sha256':p['readiness_sha256']}
                                for p in signed_shards],
            'limitation':'Collector time and host metadata are untrusted declarations; only signed-byte and pinned-role consistency is established'}
