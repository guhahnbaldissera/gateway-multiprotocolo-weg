"""
Modelo canonico independente de protocolo.

Este modulo NAO importa nenhuma biblioteca de protocolo industrial.
Ele define os 5 pilares exigidos pelo desafio WEG:
    1. SIGNIFICADO   -> SemanticRef
    2. TIPO          -> DataType
    3. UNIDADE       -> unit
    4. QUALIDADE     -> Quality
    5. TIMESTAMP     -> Timestamp
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DataType(str, Enum):
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"


class Validity(str, Enum):
    GOOD = "good"
    INVALID = "invalid"
    QUESTIONABLE = "questionable"


class QualityDetail(str, Enum):
    OVERFLOW = "overflow"
    OUT_OF_RANGE = "outOfRange"
    OLD_DATA = "oldData"
    INACCURATE = "inaccurate"
    COMM_LOST = "commLost"


class MetadataOrigin(str, Enum):
    """De onde veio o metadado. Diferenciar isso e o que impede a perda silenciosa."""

    NATIVE = "native"      # o protocolo de origem carregava essa informacao
    CONFIGURED = "configured"  # veio do cadastro de pontos (de-para), nao do fio
    ASSUMED = "assumed"    # o gateway assumiu um padrao por ausencia de dado


@dataclass(frozen=True)
class Quality:
    validity: Validity = Validity.GOOD
    detail: frozenset[QualityDetail] = frozenset()
    origin: MetadataOrigin = MetadataOrigin.NATIVE

    @property
    def is_native(self) -> bool:
        return self.origin is MetadataOrigin.NATIVE

    def describe(self) -> str:
        base = self.validity.value
        if self.detail:
            base += "|" + "|".join(sorted(d.value for d in self.detail))
        return base


@dataclass(frozen=True)
class Timestamp:
    """
    value  = o instante atribuido a medida
    origin = NATIVE  -> o protocolo de origem entregou o instante de aquisicao
             ASSUMED -> o gateway usou o horario de recebimento (APROXIMADO)

    Marcar isso explicitamente e o que evita que um timestamp aproximado
    seja apresentado como se fosse o instante real da medicao.
    """

    value: datetime
    origin: MetadataOrigin = MetadataOrigin.NATIVE

    @property
    def is_approximate(self) -> bool:
        return self.origin is not MetadataOrigin.NATIVE


@dataclass(frozen=True)
class SemanticRef:
    """PILAR 1 - SIGNIFICADO. O que este numero representa no mundo fisico."""

    asset_id: str           # "TR-01"
    asset_type: str         # "power_transformer"
    measurement: str        # "oil_top_temperature"
    iec61850_hint: str | None = None   # "YPTR1.TmpOil" - ponte para o modelo 61850

    def as_path(self) -> str:
        return f"{self.asset_id}/{self.measurement}"


@dataclass
class CanonicalSample:
    """Uma medida, normalizada, independente de protocolo."""

    # --- identidade e rastreabilidade ---
    event_id: str
    sequence: int
    correlation_id: str | None = None

    # --- os 5 pilares ---
    semantic: SemanticRef = None          # 1. SIGNIFICADO
    data_type: DataType = DataType.FLOAT  # 2. TIPO
    unit: str = ""                        # 3. UNIDADE
    quality: Quality = field(default_factory=Quality)   # 4. QUALIDADE
    timestamp: Timestamp = None           # 5. TIMESTAMP

    # --- valores ---
    raw_value: Any = None       # exatamente como veio do fio
    value: Any = None           # apos escala/offset

    # --- proveniencia ---
    source_protocol: str = ""
    source_device: str = ""
    source_address: str = ""
    scale: float = 1.0
    offset: float = 0.0
    mapping_version: str = "0"
    adapter_version: str = "0"
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def new_event_id() -> str:
        return str(uuid.uuid4())

    def with_value(self, value: Any) -> "CanonicalSample":
        return replace(self, value=value)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "correlation_id": self.correlation_id,
            "asset_id": self.semantic.asset_id if self.semantic else None,
            "asset_type": self.semantic.asset_type if self.semantic else None,
            "measurement": self.semantic.measurement if self.semantic else None,
            "iec61850_hint": self.semantic.iec61850_hint if self.semantic else None,
            "data_type": self.data_type.value,
            "unit": self.unit,
            "raw_value": self.raw_value,
            "value": self.value,
            "scale": self.scale,
            "offset": self.offset,
            "quality": self.quality.describe(),
            "quality_origin": self.quality.origin.value,
            "timestamp": self.timestamp.value.isoformat() if self.timestamp else None,
            "timestamp_origin": self.timestamp.origin.value if self.timestamp else None,
            "timestamp_is_approximate": self.timestamp.is_approximate if self.timestamp else None,
            "source_protocol": self.source_protocol,
            "source_device": self.source_device,
            "source_address": self.source_address,
            "mapping_version": self.mapping_version,
            "adapter_version": self.adapter_version,
            "received_at": self.received_at.isoformat(),
        }
