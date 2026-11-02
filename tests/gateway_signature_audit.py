#!/usr/bin/env python3
"""Second-language Ed25519 validation of each persisted provider proof.

Trust boundary: verifies signatures under the lab fixture public key, the
stored metadata and expected per-key snapshots. It does NOT prove the issuer's
claim is truthful, nor is the fixture a production trust anchor.
"""
import base64
from pathlib import Path
import re
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from gateway_identity_audit import expected_keys


def decode_url(text, max_encoded):
    if (not isinstance(text,str) or not 1<=len(text)<=max_encoded or
            re.fullmatch(r'[A-Za-z0-9_-]+',text) is None):
        raise ValueError('invalid base64url proof segment')
    try:
        decoded=base64.urlsafe_b64decode(text+'='*(-len(text)%4))
    except (ValueError,base64.binascii.Error) as exc:
        raise ValueError('invalid base64url') from exc
    if base64.urlsafe_b64encode(decoded).rstrip(b'=').decode('ascii')!=text:
        raise ValueError('noncanonical base64url')
    return decoded


def verify_proof(token,expected_key,expected_nonce,expected_timestamp,public_key):
    if not isinstance(token,str) or len(token)>7000 or token.count('.')!=1:
        raise ValueError('invalid proof framing')
    data,signature=token.split('.')
    payload=decode_url(data,6000);signature=decode_url(signature,128)
    if len(payload)>2000 or len(signature)!=64:
        raise ValueError('invalid proof lengths')
    try:public_key.verify(signature,payload)
    except Exception as exc:raise ValueError('Ed25519 signature verification failed') from exc
    try:
        fields=payload.decode('ascii').split('\n')
        actual_key=decode_url(fields[3],500).decode('utf-8')
    except (ValueError,UnicodeDecodeError,IndexError) as exc:
        raise ValueError('invalid proof encoding') from exc
    if (len(fields)!=9 or fields[:3]!=['P20V1','deroyque-lab-provider','lab-key-01'] or
            actual_key!=expected_key or fields[4]!='ACTIVATED' or
            fields[5:7]!=['1','1'] or fields[8]!=expected_nonce or
            fields[7]!=str(expected_timestamp) or
            re.fullmatch(r'[1-9][0-9]{10,15}',fields[7]) is None):
        raise ValueError('incorrect signed claim, identity or stored metadata')
    return True


def audit_signed_rows(rows,run,count,public_key_path):
    keys=expected_keys(run,count)
    with Path(public_key_path).open('rb') as f:public_key=load_pem_public_key(f.read())
    seen=set()
    for line in rows.splitlines():
        parts=line.split('|')
        if len(parts)!=4:raise ValueError('corrupted signed proof SQL record')
        key,token,nonce,stamp=parts
        if key not in keys or key in seen:
            raise ValueError('missing or duplicate signed proof key')
        verify_proof(token,key,nonce,stamp,public_key)
        seen.add(key)
    if seen!=keys:raise ValueError('signed proof keys incomplete')
    return {'independently_verified_ed25519':len(seen)}


def signed_tokens_query(schema,run):
    from gateway_identity_audit import postgres_identity_query
    postgres_identity_query(schema,run) # validate identifiers before interpolating
    return f"""SELECT r.idem_key, a.token, a.nonce, a.signed_at_ms
        FROM {schema}.p9_requests r
        JOIN {schema}.p9_dispatch d ON d.rid=r.rid
        JOIN {schema}.p20_signed_attestation a ON a.did=d.did
        WHERE left(r.idem_key,length('{run}-'))='{run}-'
        ORDER BY r.idem_key"""
