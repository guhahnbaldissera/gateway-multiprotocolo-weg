"""
Adaptador de ORIGEM - OPC UA.

Toda dependencia de asyncua esta confinada neste arquivo (junto com opcua_sink.py).

OPC UA como origem e o caso mais rico de todos: o DataValue devolvido pelo
servidor ja carrega StatusCode (qualidade) e SourceTimestamp nativamente -
diferente de Modbus e DNP3, aqui a qualidade "ruim" e informada pelo PROPRIO
protocolo, nao inferida por faixa de valor.
"""

from __future__ import annotations

from asyncua.sync import Client

from core.canonical import MetadataOrigin, Quality, Validity
from core.ports import RawReading, SourceAdapter


class OpcUaSource(SourceAdapter):
    protocol_name = "opcua"
    adapter_version = "1.0"

    def __init__(self, endpoint: str = "opc.tcp://localhost:4841/weg/sensor/"):
        self.endpoint = endpoint
        self._client: Client | None = None

    def connect(self) -> None:
        self._client = Client(self.endpoint, timeout=3)
        self._client.connect()

    @property
    def connected(self) -> bool:
        return self._client is not None

    @staticmethod
    def _decode_quality(status) -> Quality:
        if status.is_good():
            validade = Validity.GOOD
        elif status.is_uncertain():
            validade = Validity.QUESTIONABLE
        else:
            validade = Validity.INVALID
        return Quality(validity=validade, origin=MetadataOrigin.NATIVE)

    def read(self, address: str) -> RawReading:
        """address = caminho do node, ex: '2:Sensores/2:AmbientTemperature'
        ou o node_id completo (ex: 'ns=2;s=AmbientTemperature')."""
        if self._client is None:
            return RawReading(address=address, raw_value=None,
                              error="cliente OPC UA nao conectado")
        try:
            node = self._client.get_node(address)
            dv = node.read_data_value()

            return RawReading(
                address=address,
                raw_value=dv.Value.Value,
                native_quality=self._decode_quality(dv.StatusCode),
                native_timestamp=dv.SourceTimestamp,
            )
        except Exception as e:
            return RawReading(address=address, raw_value=None,
                              error=f"perda de comunicacao com servidor OPC UA "
                                    f"{self.endpoint} ({e})")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            finally:
                self._client = None
