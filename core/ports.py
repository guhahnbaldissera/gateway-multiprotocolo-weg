"""
Portas (interfaces) entre o nucleo e os adaptadores de protocolo.

REQUISITO WEG: "as estrategias abordadas nao deverao depender diretamente de
bibliotecas atreladas aos protocolos industriais".

Este arquivo e a fronteira. O nucleo conversa SOMENTE com estas abstracoes.
Nenhum tipo de pymodbus / pyiec61850 / asyncua atravessa esta linha.
Trocar a biblioteca de um protocolo afeta apenas o arquivo do adaptador.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .canonical import CanonicalSample, DataType, Quality, Timestamp


# ---------------------------------------------------------------------------
# Leitura crua vinda de um protocolo de origem
# ---------------------------------------------------------------------------

@dataclass
class RawReading:
    """
    O que o adaptador de ORIGEM devolve.

    Repare: quality e timestamp sao OPCIONAIS. Modbus nao tem nenhum dos dois;
    IEC 61850 tem os dois. O nucleo decide o que fazer com a ausencia -
    o adaptador nunca inventa dado por conta propria.
    """

    address: str
    raw_value: Any
    native_quality: Quality | None = None
    native_timestamp: datetime | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Capacidades declaradas de um protocolo de destino
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SinkCapabilities:
    """
    O que este protocolo de destino CONSEGUE representar.

    Esta declaracao e o que torna a deteccao de perda semantica AUTOMATICA:
    o nucleo compara a amostra canonica com estas capacidades e deriva a perda,
    em vez de ter regras hard-coded por par de protocolos.

    Consequencia pratica: um protocolo novo ganha analise de perda de graca,
    bastando declarar suas capacidades.
    """

    supports_quality: bool
    supports_timestamp: bool
    supports_unit: bool
    supports_float: bool
    supports_semantic_model: bool   # consegue dizer "isto e um transformador"?
    numeric_bits: int               # 16 = Modbus register, 32/64 = float nativo
    quality_states: int             # quantos estados distintos de qualidade

    def describe(self) -> str:
        eixos = []
        eixos.append(f"qualidade={'sim' if self.supports_quality else 'NAO'}")
        eixos.append(f"timestamp={'sim' if self.supports_timestamp else 'NAO'}")
        eixos.append(f"unidade={'sim' if self.supports_unit else 'NAO'}")
        eixos.append(f"modelo_semantico={'sim' if self.supports_semantic_model else 'NAO'}")
        eixos.append(f"bits={self.numeric_bits}")
        return ", ".join(eixos)


@dataclass
class PublishResult:
    ok: bool
    detail: str = ""
    encoded_payload: Any = None


@dataclass(frozen=True)
class TargetBinding:
    """
    Como este ponto deve ser gravado no protocolo de destino.

    Existe separado da escala de ORIGEM de proposito: normalizar o registrador
    cru em 25.3 Cel e uma coisa; codificar 25.3 Cel de volta no registrador do
    SCADA do cliente e outra, com fator proprio. O de-para tem dois lados
    independentes.
    """

    address: str
    scale: float = 1.0
    offset: float = 0.0


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

class SourceAdapter(ABC):
    """Adaptador de ENTRADA. Fala um protocolo, devolve leitura crua."""

    protocol_name: str = "undefined"
    adapter_version: str = "0"

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def read(self, address: str) -> RawReading: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...


class SinkAdapter(ABC):
    """Adaptador de SAIDA. Recebe amostra canonica, codifica no protocolo destino."""

    protocol_name: str = "undefined"
    adapter_version: str = "0"

    @property
    @abstractmethod
    def capabilities(self) -> SinkCapabilities: ...

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def publish(self, sample: CanonicalSample, binding: TargetBinding) -> PublishResult: ...

    @abstractmethod
    def close(self) -> None: ...
