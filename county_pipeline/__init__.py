"""County records pipeline — reference implementation.

Watches a county portal for changed lien filings, reads each document, normalizes and
de-duplicates the rows, enriches them against a second source, routes anything incomplete
to a human review queue, aborts a batch that looks mostly wrong, tells a quiet county apart
from a stale scraper, and delivers clean rows to a CRM tab that has exactly one writer.
Synthetic data only ("Marlow County" is fictional). Production code is private.
"""
