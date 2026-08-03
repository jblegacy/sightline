"""The provenance validator. See CLAUDE.md — gates every document build.

Do not add a bypass flag, a "force" option, or a try/except that swallows
ProvenanceError. If a bullet fails here, assembly stops.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

QUALIFIERS = ("estimated", "modeled", "projected", "approximately")


class ProvenanceError(Exception):
    """A bullet is not safe to ship. Assembly must stop, not catch this."""


class HasProvenance(Protocol):
    ref: str
    text: str
    provenance: str
    status: str


@dataclass(frozen=True)
class Bullet:
    """Minimal shape the validator needs. The `bullets` table row satisfies this."""

    ref: str
    text: str
    provenance: str
    status: str


def assert_shippable(bullets: Iterable[HasProvenance]) -> None:
    """Raise ProvenanceError on the first bullet that isn't safe to ship.

    provenance values: measured | stated | modeled | derived
    """
    for b in bullets:
        if b.provenance == "derived":
            raise ProvenanceError(f"{b.ref}: derived claims never ship")
        if b.provenance == "modeled" and not any(q in b.text.lower() for q in QUALIFIERS):
            raise ProvenanceError(f"{b.ref}: modeled claim needs an explicit qualifier")
        if b.status != "verified":
            raise ProvenanceError(f"{b.ref}: status={b.status}")
