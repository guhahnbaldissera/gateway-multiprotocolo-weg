"""
Motor de traducao. Orquestra origem -> canonico -> destino.

Este modulo e o nucleo da solucao e NAO importa nenhuma biblioteca de protocolo.
Ele so conhece as abstracoes de ports.py. Trocar pymodbus por outra biblioteca
nao altera uma linha deste arquivo.

Ciclo de vida de um evento (a ordem importa):
    1. ler do protocolo de origem
    2. validar configuracao e faixa        -> invalido vai para quarantine
    3. normalizar para o modelo canonico
    4. PERSISTIR como pending              <- antes de qualquer publicacao
    5. analisar perda semantica vs destino
    6. publicar
    7. delivered  ou  retry (reenvio posterior)
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

from .canonical import (CanonicalSample, MetadataOrigin, Quality, SemanticRef,
                        Timestamp, Validity)
from .loss import LossAnalyzer, LossRecord
from .ports import RawReading, SinkAdapter, SourceAdapter
from .registry import MappingRegistry, PointMapping
from .store import EventState, Store


class TranslationEngine:
    def __init__(self, registry: MappingRegistry, store: Store,
                 sources: dict[str, SourceAdapter],
                 sinks: dict[str, SinkAdapter],
                 allowed_devices: set[str] | None = None):
        self.registry = registry
        self.store = store
        self.sources = sources
        self.sinks = sinks
        self.allowed_devices = allowed_devices
        self.analyzer = LossAnalyzer()
        self._seq = itertools.count(1)
        self.on_event = None   # callback opcional para a UI/CLI

    # ------------------------------------------------------------------
    # 1-3: leitura e normalizacao
    # ------------------------------------------------------------------

    def _build_sample(self, mapping: PointMapping, reading: RawReading,
                      adapter_version: str) -> CanonicalSample:
        agora = datetime.now(timezone.utc)

        # QUALIDADE: nativa se o protocolo entregou; assumida caso contrario.
        if reading.native_quality is not None:
            qualidade = reading.native_quality
        else:
            qualidade = Quality(validity=Validity.GOOD,
                                origin=MetadataOrigin.ASSUMED)

        # TIMESTAMP: nativo se o protocolo entregou; aproximado caso contrario.
        if reading.native_timestamp is not None:
            estampa = Timestamp(value=reading.native_timestamp,
                                origin=MetadataOrigin.NATIVE)
        else:
            estampa = Timestamp(value=agora, origin=MetadataOrigin.ASSUMED)

        return CanonicalSample(
            event_id=CanonicalSample.new_event_id(),
            sequence=next(self._seq),
            semantic=SemanticRef(
                asset_id=mapping.asset_id,
                asset_type=mapping.asset_type,
                measurement=mapping.measurement,
                iec61850_hint=mapping.iec61850_hint),
            data_type=mapping.data_type,
            unit=mapping.unit,
            quality=qualidade,
            timestamp=estampa,
            raw_value=reading.raw_value,
            value=mapping.normalize(reading.raw_value),
            source_protocol=mapping.source_protocol,
            source_device=mapping.source_device,
            source_address=mapping.source_address,
            scale=mapping.scale,
            offset=mapping.offset,
            mapping_version=self.registry.version,
            adapter_version=adapter_version,
            received_at=agora,
        )

    # ------------------------------------------------------------------
    # ciclo completo de um ponto
    # ------------------------------------------------------------------

    def process_point(self, mapping: PointMapping) -> dict:
        resultado = {"point_id": mapping.point_id, "state": None,
                     "losses": [], "sample": None, "detail": ""}

        # --- controle de acesso: negacao por padrao ---
        if self.allowed_devices is not None and mapping.source_device not in self.allowed_devices:
            self.store.audit("device_rejected",
                             f"{mapping.source_device} nao esta na allowlist")
            resultado.update(state="rejected",
                             detail=f"dispositivo '{mapping.source_device}' nao autorizado")
            return resultado

        source = self.sources.get(mapping.source_protocol)
        if source is None:
            resultado.update(state="rejected",
                             detail=f"sem adaptador para protocolo '{mapping.source_protocol}'")
            return resultado

        # --- 1. leitura ---
        leitura = source.read(mapping.source_address)

        if not leitura.ok:
            # PERDA DE COMUNICACAO: nao existe amostra para enfileirar,
            # mas o incidente fica registrado na auditoria.
            self.store.audit("read_failed",
                             f"{mapping.point_id}: {leitura.error}")
            resultado.update(state="comm_error", detail=leitura.error)
            return resultado

        # --- 2. validacao de faixa ---
        valor_normalizado = mapping.normalize(leitura.raw_value)
        motivo = mapping.validate_value(valor_normalizado)

        amostra = self._build_sample(mapping, leitura, source.adapter_version)
        resultado["sample"] = amostra

        if motivo is not None:
            # valor absurdo NAO e descartado: vai para quarentena com justificativa
            self.store.enqueue(amostra, mapping.point_id,
                               state=EventState.QUARANTINE, error=motivo)
            self.store.audit("quarantined", f"{mapping.point_id}: {motivo}")
            resultado.update(state=EventState.QUARANTINE.value, detail=motivo)
            return resultado

        # --- 4. persistir ANTES de publicar ---
        novo = self.store.enqueue(amostra, mapping.point_id, state=EventState.PENDING)
        if not novo:
            resultado.update(state="duplicate", detail="event_id ja processado")
            return resultado

        # --- 5/6/7: publicar ---
        estado, perdas, detalhe = self.publish(amostra, mapping)
        resultado.update(state=estado.value, losses=perdas, detail=detalhe)

        if self.on_event:
            self.on_event(resultado)
        return resultado

    # ------------------------------------------------------------------
    # publicacao + analise de perda
    # ------------------------------------------------------------------

    def publish(self, sample: CanonicalSample,
                mapping: PointMapping) -> tuple[EventState, list[LossRecord], str]:
        sink = self.sinks.get(mapping.target_protocol)
        if sink is None:
            erro = f"sem adaptador de destino para '{mapping.target_protocol}'"
            self.store.mark(sample.event_id, EventState.RETRY, erro, bump_attempts=True)
            return EventState.RETRY, [], erro

        # 5. perda semantica derivada das capacidades declaradas do destino
        perdas = self.analyzer.analyze(sample, sink.capabilities, sink.protocol_name)
        if perdas:
            self.store.record_losses(perdas)

        # 6. publicar (o binding carrega a escala do DESTINO, nao a da origem)
        r = sink.publish(sample, mapping.binding())

        # 7. estado final
        if r.ok:
            self.store.mark(sample.event_id, EventState.DELIVERED)
            return EventState.DELIVERED, perdas, r.detail
        self.store.mark(sample.event_id, EventState.RETRY, r.detail, bump_attempts=True)
        return EventState.RETRY, perdas, r.detail

    # ------------------------------------------------------------------
    # reenvio da fila (store-and-forward)
    # ------------------------------------------------------------------

    def flush_queue(self, limit: int = 500) -> dict:
        """
        Reenvia o que ficou parado enquanto o destino estava fora.
        Nenhum evento e descartado por ter falhado antes.
        """
        entregues, falhas = 0, 0
        for row in self.store.pending_batch(limit):
            mapping = self.registry.get(row["point_id"])
            if mapping is None:
                continue
            sink = self.sinks.get(mapping.target_protocol)
            if sink is None:
                falhas += 1
                continue

            import json
            payload = json.loads(row["payload"])
            r = sink.publish_raw(payload, mapping.binding()) \
                if hasattr(sink, "publish_raw") else None

            if r is None:
                falhas += 1
                continue
            if r.ok:
                self.store.mark(row["event_id"], EventState.DELIVERED)
                entregues += 1
            else:
                self.store.mark(row["event_id"], EventState.RETRY, r.detail,
                                bump_attempts=True)
                falhas += 1

        self.store.audit("queue_flush", f"entregues={entregues} falhas={falhas}")
        return {"delivered": entregues, "failed": falhas}

    # ------------------------------------------------------------------

    def run_cycle(self) -> list[dict]:
        return [self.process_point(p) for p in self.registry.all()]
