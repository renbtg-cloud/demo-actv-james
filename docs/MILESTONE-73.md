# Milestone 73 — Complete per-shard evidence inventory and integrity gate

An independent collector can execute `simulator/distributed_integrity.py --seal --index <private-index> <all-shard-folders>`. The resulting SHA-256 inventory lists every `summary.json` and expected `agent-NNN.tsv` with their exact size. The merge accepts `--evidence-index` and refuses truncated, modified, unexpected, duplicate or symlinked records. Per-file and total byte bounds reject unsafe evidence inputs. The index must remain separately controlled by the collector.

Digests prove bytes did not change relative to the index; they do not prove the producer was honest, nor authenticate the remote host, nor prove data was collected before tampering. Evidence sealing is a test-laboratory integrity mechanism, not a signature or hardware attestation.
