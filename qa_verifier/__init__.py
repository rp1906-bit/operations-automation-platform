"""QA Verifier — reference implementation.

Judges a pipeline run by its output, not its exit code (GREEN / YELLOW / UNVERIFIED / RED,
where UNVERIFIED means the checker itself could not check); names the root cause with a
confidence level; picks a lane (auto-execute / draft / escalate) under hard circuit
breakers; reports in plain English; keeps an append-only ledger.
Synthetic data only ("Marlow County"). Production code is private.
"""
