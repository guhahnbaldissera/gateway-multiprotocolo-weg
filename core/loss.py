"""
Deteccao e registro de perda semantica.

REQUISITO WEG: "a solucao devera identificar e explicitar as possiveis perdas ou
incompatibilidades de informacao durante a conversao entre protocolos" e definir
"o comportamento quando o protocolo de destino nao tiver capacidade para
representar todos os metadados existentes no protocolo de origem".

Principio central: NENHUM DADO SE PERDE EM SILENCIO.
Toda degradacao vira um registro explicito, rastreavel ate o evento de origem.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum

from .canonical import CanonicalSample, DataType, MetadataOrigin
from .ports import SinkCapabilities


class LossType(str, Enum):
    DROPPED = "dropped"            # metadado existia na origem e nao cabe no destino
    APPROXIMATED = "approximated"  # representado com menor fidelidade
    SYNTHESIZED = "synthesized"    # nao existia na origem e foi preenchido pelo gateway
    DEGRADED = "degraded"          # taxonomia rica reduzida a uma pobre


@dataclass
class LossRecord:
    event_id: str
    field: str
    loss_type: LossType
    source_value: str
    target_value: str
    reason: str
    mapping_version: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["loss_type"] = self.loss_type.value
        return d

    def human(self) -> str:
        return (f"[{self.loss_type.value.upper():<12}] {self.field:<22} "
                f"{self.source_value!r} -> {self.target_value!r}  ({self.reason})")


class LossAnalyzer:
    """
    Compara uma amostra canonica com as capacidades declaradas do destino
    e deriva a lista de perdas. Nao ha regra por par de protocolos:
    a regra e a diferenca entre o que a amostra carrega e o que o destino aceita.
    """

    def analyze(self, sample: CanonicalSample, caps: SinkCapabilities,
                target_protocol: str) -> list[LossRecord]:
        losses: list[LossRecord] = []

        def add(field, loss_type, src, tgt, reason):
            losses.append(LossRecord(
                event_id=sample.event_id,
                field=field,
                loss_type=loss_type,
                source_value=str(src),
                target_value=str(tgt),
                reason=reason,
                mapping_version=sample.mapping_version,
            ))

        # --- QUALIDADE ---
        if not caps.supports_quality:
            if sample.quality.is_native:
                add("quality", LossType.DROPPED, sample.quality.describe(), "-",
                    f"{target_protocol} nao possui campo de qualidade; "
                    f"flags originais preservadas apenas no historico do gateway")
        elif sample.quality.is_native and caps.quality_states < 8:
            # 61850 tem bit-string rica; DNP3 tem 1 byte de flags
            if sample.quality.detail:
                add("quality.detail", LossType.DEGRADED, sample.quality.describe(),
                    sample.quality.validity.value,
                    f"{target_protocol} representa apenas {caps.quality_states} estados; "
                    f"sub-flags detalhadas nao tem equivalente")

        # --- TIMESTAMP ---
        if sample.timestamp is not None:
            if not caps.supports_timestamp:
                add("timestamp", LossType.DROPPED,
                    sample.timestamp.value.isoformat(), "-",
                    f"{target_protocol} nao transporta estampa de tempo; "
                    f"o consumidor vera apenas o instante em que leu o registrador")
            elif sample.timestamp.is_approximate:
                add("timestamp", LossType.SYNTHESIZED,
                    "ausente na origem", sample.timestamp.value.isoformat(),
                    "protocolo de origem nao carrega instante de aquisicao; "
                    "usado o horario de recebimento do gateway (APROXIMADO)")

        # --- UNIDADE ---
        if sample.unit and not caps.supports_unit:
            add("unit", LossType.DROPPED, sample.unit, "-",
                f"{target_protocol} nao carrega unidade de engenharia; "
                f"acordo deve ser feito fora de banda (mapa de pontos)")

        # --- SIGNIFICADO / MODELO SEMANTICO ---
        if sample.semantic and not caps.supports_semantic_model:
            add("semantic", LossType.DROPPED,
                sample.semantic.iec61850_hint or sample.semantic.as_path(),
                "endereco numerico",
                f"{target_protocol} nao possui modelo de informacao; "
                f"o significado vive apenas no cadastro de pontos externo")

        # --- TIPO / RESOLUCAO NUMERICA ---
        if sample.data_type is DataType.FLOAT and not caps.supports_float:
            add("data_type", LossType.APPROXIMATED, f"float({sample.value})",
                f"int{caps.numeric_bits}",
                f"{target_protocol} transporta apenas inteiros de {caps.numeric_bits} bits; "
                f"valor escalonado, fracao perdida conforme fator de escala")

        # --- ORIGEM DE METADADO CONFIGURADO ---
        if sample.quality.origin is MetadataOrigin.ASSUMED and caps.supports_quality:
            add("quality", LossType.SYNTHESIZED, "ausente na origem",
                sample.quality.describe(),
                "origem nao informa qualidade; gateway assumiu valor padrao - "
                "o destino recebera uma qualidade que nao foi medida")

        return losses


def loss_matrix(caps_by_protocol: dict[str, SinkCapabilities]) -> str:
    """Matriz de capacidades - usada como evidencia na apresentacao."""
    linhas = ["| Protocolo | Qualidade | Timestamp | Unidade | Modelo semantico | Bits |",
              "|---|---|---|---|---|---|"]
    for nome, c in caps_by_protocol.items():
        linhas.append(
            f"| {nome} | {'sim' if c.supports_quality else 'NAO'} "
            f"| {'sim' if c.supports_timestamp else 'NAO'} "
            f"| {'sim' if c.supports_unit else 'NAO'} "
            f"| {'sim' if c.supports_semantic_model else 'NAO'} "
            f"| {c.numeric_bits} |")
    return "\n".join(linhas)
