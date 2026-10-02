"""Temizleme raporu için veri yapıları."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Field:
    group: str
    name: str
    value: str = ""

    @property
    def key(self) -> str:
        return f"{self.group}:{self.name}"


@dataclass
class Kept:
    what: str
    reason: str


@dataclass
class Gate:
    name: str
    passed: bool
    details: List[str] = field(default_factory=list)


@dataclass
class Report:
    path: str
    fmt: str = ""
    found: List[Field] = field(default_factory=list)      # orijinaldeki hassas alanlar
    removed: List[str] = field(default_factory=list)      # silinen yapılar (segment, chunk, iz...)
    kept: List[Kept] = field(default_factory=list)        # bilerek bırakılanlar
    remaining: List[Field] = field(default_factory=list)  # yeniden taramada kalan hassas alanlar
    gates: List[Gate] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    error: Optional[str] = None
    scan: Dict[str, object] = field(default_factory=dict)        # orijinalin tam ExifTool taraması
    clean_scan: Dict[str, object] = field(default_factory=dict)  # temiz sürümün taraması

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.gates) == 3 and all(g.passed for g in self.gates)
