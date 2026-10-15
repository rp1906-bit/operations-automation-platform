"""Deterministic stand-in for the vision model that reads document images.

A synthetic document is either readable (a few labelled lines) or marked illegible, in which
case extraction fails the way the real reader fails on a skewed or faint scan. No OCR, no API.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadResult:
    ok: bool
    address: str = ""
    owner: str = ""
    reason: str = ""


def read_document(document: dict) -> ReadResult:
    if not document.get("readable"):
        return ReadResult(False, reason="document unreadable")
    fields = {}
    for line in document["text"].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().upper()] = value.strip()
    address, owner = fields.get("PROPERTY", ""), fields.get("GRANTEE", "")
    if not address:
        return ReadResult(False, owner=owner, reason="no property address on document")
    return ReadResult(True, address=address, owner=owner)
