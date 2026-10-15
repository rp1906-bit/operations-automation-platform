"""KPI engine, reference implementation on synthetic data.

Nightly: pull a day's caller activity (cap-aware), aggregate it, write the artifact, then decide
the exit code, then write to the shared sheet and leave a receipt of what the write returned.
Weekly: color every metric against bands, diagnose in a fixed order, deliver HTML that survives
a lossy conversion, and verify the bytes that arrived.

Callers are "Caller A/B", campaigns "Campaign A/B/C", bands illustrative. Production is private.
"""
