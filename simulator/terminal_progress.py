#!/usr/bin/env python3
"""Bounded, read-only terminal-convergence observations for disposable labs.

A single observer's monotonic clock samples authoritative PostgreSQL state.
Sampling and SQL processing add overhead; the observed DONE timestamp is an
*upper bound* on when DONE became visible, not a precise provider-effect time.
No credentials, row payloads, SQL results beyond counts or private keys are saved.
"""
import re
import threading
import time

_IDENT = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
_RUN = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')


def count_query(schema, run_id):
    if not isinstance(schema, str) or not _IDENT.fullmatch(schema):
        raise ValueError('unsafe PostgreSQL schema identifier')
    if not isinstance(run_id, str) or not _RUN.fullmatch(run_id):
        raise ValueError('unsafe run identifier')
    prefix = run_id + '-'
    return (f"SELECT count(*), count(*) FILTER (WHERE stage='DONE'), "
            f"count(*) FILTER (WHERE stage='FAILED') FROM {schema}.p9_requests "
            f"WHERE left(idem_key,length('{prefix}'))='{prefix}'")


class CompletionTrace:
    """Strict monotonic counter transitions with bounded memory and no IDs."""
    def __init__(self, keys, max_samples=1200):
        if type(keys) is not int or not 1 <= keys <= 100_000:
            raise ValueError('invalid expected key count')
        if type(max_samples) is not int or not 2 <= max_samples <= 100_000:
            raise ValueError('invalid sampling budget')
        self.keys = keys
        self.max_samples = max_samples
        self.samples = []

    def observe(self, text, ns, query_start_ns=None):
        if type(ns) is not int or ns <= 0:
            raise ValueError('invalid monotonic time')
        if query_start_ns is None:
            query_start_ns = ns  # Zero-length read in deterministic contract tests.
        if type(query_start_ns) is not int or query_start_ns <= 0 or query_start_ns > ns:
            raise ValueError('invalid query start/end monotonic interval')
        if not isinstance(text, str) or not re.fullmatch(r'[0-9]+\|[0-9]+\|[0-9]+', text.strip()):
            raise ValueError('malformed aggregate PostgreSQL counters')
        admitted, done, failed = map(int, text.strip().split('|'))
        if not (0 <= done + failed <= admitted <= self.keys):
            raise ValueError('impossible admitted / terminal counters')
        if self.samples:
            prev = self.samples[-1]
            if ns <= prev[0] or query_start_ns < prev[0] or admitted < prev[1] or done < prev[2] or failed < prev[3]:
                raise ValueError('clock regression or counter regression')
        if len(self.samples) >= self.max_samples:
            raise ValueError('terminal observer sample budget exceeded')
        self.samples.append((ns, admitted, done, failed, query_start_ns))

    @property
    def complete(self):
        return bool(self.samples and self.samples[-1][2] == self.keys)

    def report(self):
        if len(self.samples) < 2 or self.samples[0][1:4] != (0, 0, 0):
            raise ValueError('missing uncontaminated zero baseline')
        if not self.complete or self.samples[-1][1:4] != (self.keys, self.keys, 0):
            raise ValueError('terminal completion not actually observed')
        # First observed admission is an UPPER observation timestamp for the
        # true first admission, while the previous zero reading is its LOWER
        # bound. The terminal transition is bounded in exactly the same way.
        # Reporting the difference of two UPPER timestamps alone is NOT a
        # confidence bound on actual convergence latency.
        first_index = next(i for i, s in enumerate(self.samples) if s[1] > 0)
        terminal_index = next(i for i, s in enumerate(self.samples) if s[2] == self.keys)
        first = self.samples[first_index]
        last = self.samples[terminal_index]
        # Query results represent a snapshot DURING the SQL round trip, not
        # necessarily at the moment the response was consumed. Previous zero
        # and incomplete snapshots therefore bound transitions using their
        # query START time, not their query END time.
        admission_before = self.samples[first_index - 1][4]
        terminal_before = self.samples[terminal_index - 1][4]
        first_done = next(s for s in self.samples if s[2] > 0)
        lower_ns = max(0, terminal_before - first[0])
        upper_ns = last[0] - admission_before
        sample_gap_ns = max(self.samples[i][0] - self.samples[i-1][4]
                            for i in range(1, len(self.samples)))
        query_duration_ns = max(s[0]-s[4] for s in self.samples)
        return {'schema_version': 1, 'scope': 'one_observer_postgresql_terminal_visibility',
                'expected_keys': self.keys, 'observed_admitted': last[1],
                'observed_done': last[2], 'observed_failed': last[3],
                'samples': len(self.samples),
                'first_admission_observed_ms': round((first[0]-self.samples[0][0])/1e6,3),
                'first_done_observed_ms': round((first_done[0]-self.samples[0][0])/1e6,3),
                'all_done_observed_ms': round((last[0]-self.samples[0][0])/1e6,3),
                'last_incomplete_sample_ms': round((terminal_before-self.samples[0][0])/1e6,3),
                'observed_convergence_ms': round((last[0]-first[0])/1e6,3),
                'sampling_uncertainty_ms': round((last[0]-terminal_before)/1e6,3),
                'first_admission_previous_zero_ms': round((admission_before-self.samples[0][0])/1e6,3),
                'convergence_lower_bound_ms': round(lower_ns/1e6,3),
                'convergence_upper_bound_ms': round(upper_ns/1e6,3),
                'convergence_bound_width_ms': round((upper_ns-lower_ns)/1e6,3),
                'max_observed_sampling_gap_ms': round(sample_gap_ns/1e6,3),
                'max_sql_round_trip_ms': round(query_duration_ns/1e6,3),
                'measurement_note': ('One monotonic clock and bounded SQL read intervals; the real '
                     'first admission occurred after the previous zero SQL snapshot and by the first positive '
                     'SQL snapshot, each of which may occur anywhere during its query round trip. The '
                     'DONE transition is similarly bracketed using query-start lower and query-end upper '
                     'timestamps. Bounds apply to PostgreSQL visibility, '
                     'not exact provider-effect time, per-key latency, or production capacity. '
                     'Polling adds overhead. The single observed-point difference is descriptive only.')}


class CompletionSampler:
    """One bounded polling thread. Every failure propagates to the caller."""
    def __init__(self, keys, query, interval_seconds=0.5, max_samples=1200):
        if not 0.1 <= interval_seconds <= 10:
            raise ValueError('sampling interval must be 0.1..10 seconds')
        self.trace = CompletionTrace(keys, max_samples)
        self.query = query
        self.interval = interval_seconds
        self.stop_event = threading.Event()
        self.thread = None
        self.error = None
        self.guard = threading.Lock()

    def _once(self):
        with self.guard:
            if self.error:
                raise self.error
            # Timestamp after read: never assign the time before a blocking DB call.
            query_start = time.monotonic_ns()
            result = self.query()
            self.trace.observe(result, time.monotonic_ns(), query_start_ns=query_start)

    def _work(self):
        try:
            while not self.stop_event.wait(self.interval) and not self.trace.complete:
                self._once()
        except Exception as exc:
            # A daemon thread must not lose unexpected sampling failures.
            self.error = exc

    def start(self):
        if self.thread is not None:
            raise ValueError('observer already started')
        self._once()  # A verified clean baseline is mandatory.
        if self.trace.samples[0][1:4] != (0,0,0):
            raise ValueError('observation began after admissions')
        self.thread = threading.Thread(target=self._work, daemon=True, name='terminal-progress')
        self.thread.start()
        return self

    def finish(self):
        if self.thread is None:
            raise ValueError('observer not started')
        self.stop_event.set()
        self.thread.join(timeout=12)
        if self.thread.is_alive():
            raise RuntimeError('terminal observer thread could not stop')
        if self.error:
            raise self.error
        if not self.trace.complete:
            self._once()
        return self.trace.report()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=12)
