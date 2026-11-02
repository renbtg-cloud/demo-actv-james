# Milestone 47 — No premature quiescence during client response delivery

A gateway's upstream response may finish before it finishes sending the response to the producer. Previously, the witness decremented `inflight` when the upstream reply arrived; a concurrent metric snapshot could incorrectly show `inflight=0` before the downstream socket write completed or failed.

The witness now records **upstream timestamp separately**, but only finalizes the counted POST and releases its concurrency permit **after attempting downstream delivery**. It keeps `inflight` positive across the intervening interval and captures failures at that boundary. The concurrent regression deliberately holds a response at this boundary, observes the in-flight count and requires the snapshot to remain incomplete until the hold is released.

As always, successful OS socket write is not authenticated evidence that the remote application received, parsed or acted on the bytes. The load generator's independent records remain necessary.
