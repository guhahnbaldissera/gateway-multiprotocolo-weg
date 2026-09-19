"""
Adaptador de DESTINO - OPC UA.

Toda dependencia de asyncua esta confinada neste arquivo.

OPC UA e o destino RICO: consegue carregar praticamente todos os metadados
do modelo canonico.
    - DataValue = valor + StatusCode (qualidade) + SourceTimestamp + ServerTimestamp
    - EngineeringUnits como Property padronizada
    - AddressSpace tipado (modelo de informacao)

Existe Companion Specification oficial "OPC UA for IEC 61850" (OPC Foundation
com IEC TC57) mapeando Common Data Classes e Logical Nodes para tipos OPC UA -
precedente direto para este caminho de conversao.
"""

from __future__ import annotations

from asyncua import ua
from asyncua.sync import Server

from core.canonical import CanonicalSample, MetadataOrigin, Validity
from core.ports import (PublishResult, SinkAdapter, SinkCapabilities,
                        TargetBinding)


_STATUS = {
    Validity.GOOD: ua.StatusCodes.Good,
    Validity.INVALID: ua.StatusCodes.Bad,
    Validity.QUESTIONABLE: ua.StatusCodes.Uncertain,
}


class OpcUaSink(SinkAdapter):
    protocol_name = "opcua"
    adapter_version = "1.0"

    def __init__(self, endpoint: str = "opc.tcp://0.0.0.0:4840/weg/gateway/",
                 namespace: str = "http://weg.hackathon/gateway"):
        self.endpoint = endpoint
        self.namespace = namespace
        self._server: Server | None = None
        self._idx = None
        self._nodes: dict[str, object] = {}
        self._folder = None

    @property
    def capabilities(self) -> SinkCapabilities:
        return SinkCapabilities(
            supports_quality=True,
            supports_timestamp=True,
            supports_unit=True,
            supports_float=True,
            supports_semantic_model=True,
            numeric_bits=64,
            quality_states=32,
        )

    def connect(self) -> None:
        self._server = Server()
        self._server.set_endpoint(self.endpoint)
        self._server.set_server_name("WEG Gateway Multiprotocolo")
        self._idx = self._server.register_namespace(self.namespace)
        self._folder = self._server.nodes.objects.add_folder(self._idx, "Ativos")
        self._server.start()

    def _node_for(self, sample: CanonicalSample, target_address: str):
        if target_address in self._nodes:
            return self._nodes[target_address]

        nome = target_address or sample.semantic.as_path()
        no = self._folder.add_variable(self._idx, nome, 0.0)
        no.set_writable()

        # UNIDADE: OPC UA carrega isso nativamente (EngineeringUnits)
        if sample.unit:
            try:
                no.add_property(self._idx, "EngineeringUnits", sample.unit)
            except Exception:
                pass
        # SIGNIFICADO: preservado como propriedades do no
        if sample.semantic:
            try:
                no.add_property(self._idx, "AssetId", sample.semantic.asset_id)
                no.add_property(self._idx, "AssetType", sample.semantic.asset_type)
                no.add_property(self._idx, "Measurement", sample.semantic.measurement)
                if sample.semantic.iec61850_hint:
                    no.add_property(self._idx, "IEC61850Ref", sample.semantic.iec61850_hint)
            except Exception:
                pass

        self._nodes[target_address] = no
        return no

    def publish(self, sample: CanonicalSample, binding: TargetBinding) -> PublishResult:
        if self._server is None:
            return PublishResult(ok=False, detail="servidor OPC UA nao iniciado")

        target_address = binding.address
        try:
            no = self._node_for(sample, target_address)

            # QUALIDADE -> StatusCode nativo
            status = ua.StatusCode(_STATUS.get(sample.quality.validity,
                                               ua.StatusCodes.Uncertain))

            # TIMESTAMP -> SourceTimestamp nativo.
            # Se o timestamp for aproximado (origem nao tinha), o gateway ja
            # registrou isso como perda SYNTHESIZED - o dado segue rastreavel.
            dv = ua.DataValue(
                Value=ua.Variant(float(sample.value), ua.VariantType.Double),
                StatusCode=status,
                SourceTimestamp=sample.timestamp.value if sample.timestamp else None,
            )
            no.set_value(dv)

            return PublishResult(
                ok=True,
                detail=f"publicado em {target_address}",
                encoded_payload={
                    "node": target_address,
                    "value": float(sample.value),
                    "status": sample.quality.validity.value,
                    "source_timestamp": sample.timestamp.value.isoformat()
                    if sample.timestamp else None,
                    "units": sample.unit,
                })
        except Exception as e:
            return PublishResult(ok=False, detail=f"falha ao publicar em OPC UA: {e}")

    def publish_raw(self, payload: dict, binding: TargetBinding) -> PublishResult:
        """Reenvio a partir da fila persistida (payload ja serializado)."""
        if self._server is None:
            return PublishResult(ok=False, detail="servidor OPC UA nao iniciado")
        target_address = binding.address
        try:
            if target_address not in self._nodes:
                no = self._folder.add_variable(self._idx, target_address, 0.0)
                no.set_writable()
                self._nodes[target_address] = no
            self._nodes[target_address].set_value(float(payload["value"]))
            return PublishResult(ok=True, detail="reenviado da fila")
        except Exception as e:
            return PublishResult(ok=False, detail=str(e))

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None
