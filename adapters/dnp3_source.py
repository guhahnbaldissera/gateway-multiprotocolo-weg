"""
Adaptador de ORIGEM - DNP3  [DADOS SIMULADOS, sem pilha de protocolo real]

POR QUE SIMULADO, NAO REAL:
A stack de referencia OpenDNP3 esta ARQUIVADA no GitHub desde 2022-05-18, e o
binding Python pydnp3 nao recebe release desde 2018-06-01 (verificado em
2026-09-19). Colocar caminho critico do hackathon sobre dependencia morta seria
imprudente. O PDF do desafio permite explicitamente "dados mockados" - e o que
este adaptador faz, respeitando a SEMANTICA real do protocolo (nao so o valor).

MODELO DE ENDERECAMENTO DNP3 REAL:
    <group>.<variation>.<index>
Exemplos:
    30.5.1  -> Group 30 Var 5  (Analog Input, 32-bit, sem timestamp)
    32.7.1  -> Group 32 Var 7  (Analog Input Event, 32-bit float + timestamp)

QUALIDADE (flags de 1 byte, IEEE 1815):
    bit 0 ONLINE · bit 1 RESTART · bit 2 COMM_LOST · bit 3 REMOTE_FORCED
    bit 4 LOCAL_FORCED · bit 5 OVER_RANGE · bit 6 REFERENCE_ERR

Isso e deliberadamente mais pobre que o 'q' do IEC 61850 (bit-string detalhada):
o DNP3 so tem 8 flags binarios, sem os niveis de detalhe do 61850. A conversao
DNP3 -> OPC UA "sobe" essa granularidade grosseira para o StatusCode rico do
OPC UA - o oposto do caso MMS -> Modbus, que "desce" para nada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.canonical import MetadataOrigin, Quality, QualityDetail, Validity
from core.ports import RawReading, SourceAdapter

# bits do byte de flags DNP3
FLAG_ONLINE = 1 << 0
FLAG_RESTART = 1 << 1
FLAG_COMM_LOST = 1 << 2
FLAG_REMOTE_FORCED = 1 << 3
FLAG_LOCAL_FORCED = 1 << 4
FLAG_OVER_RANGE = 1 << 5
FLAG_REFERENCE_ERR = 1 << 6


@dataclass
class SimulatedDnp3Device:
    """
    Um 'outstation' DNP3 simulado - representa, por exemplo, um banco de
    baterias de subestacao (ativo citado na lista de variaveis do TFM).

    Controlavel para os cenarios de demonstracao:
        set_offline(True)  -> simula falta de sensor  (comm error)
        set_fault(True)    -> simula problema mecanico (flags + valor implausivel)
    """

    baseline: float = 48.0     # tensao nominal de um banco de baterias, volts
    _offline: bool = False
    _fault: bool = False

    def set_offline(self, valor: bool) -> None:
        self._offline = valor

    def set_fault(self, valor: bool) -> None:
        self._fault = valor

    def poll(self) -> tuple[int, int, datetime | None]:
        """Retorna (valor_bruto_x100, flags, timestamp_ou_None)."""
        if self._offline:
            raise ConnectionError("outstation DNP3 nao respondeu ao poll (timeout)")

        if self._fault:
            # tensao implausivel + flags indicando range estourado
            valor = int(round(9999 * 100))
            flags = FLAG_ONLINE | FLAG_OVER_RANGE
        else:
            ruido = random.uniform(-0.6, 0.6)
            valor = int(round((self.baseline + ruido) * 100))
            flags = FLAG_ONLINE

        agora = datetime.now(timezone.utc)
        return valor, flags, agora


class Dnp3Source(SourceAdapter):
    protocol_name = "dnp3"
    adapter_version = "1.0-simulado"

    def __init__(self):
        self._connected = False
        self.devices: dict[str, SimulatedDnp3Device] = {
            "OUTSTATION-BAT-01": SimulatedDnp3Device(baseline=48.0),
        }

    def connect(self) -> None:
        self._connected = True

    @property
    def connected(self) -> bool:
        return self._connected

    @staticmethod
    def _parse_address(address: str) -> tuple[int, int, int]:
        partes = address.split(".")
        if len(partes) != 3:
            raise ValueError(f"endereco DNP3 invalido: '{address}' "
                             f"(esperado group.variation.index)")
        grupo, variacao, indice = (int(p) for p in partes)
        return grupo, variacao, indice

    @staticmethod
    def _decode_quality(flags: int) -> Quality:
        if not (flags & FLAG_ONLINE):
            validade = Validity.INVALID
        elif flags & (FLAG_OVER_RANGE | FLAG_REFERENCE_ERR):
            validade = Validity.QUESTIONABLE
        else:
            validade = Validity.GOOD

        detalhes = set()
        if flags & FLAG_OVER_RANGE:
            detalhes.add(QualityDetail.OUT_OF_RANGE)
        if flags & FLAG_COMM_LOST:
            detalhes.add(QualityDetail.COMM_LOST)
        if flags & (FLAG_REMOTE_FORCED | FLAG_LOCAL_FORCED):
            detalhes.add(QualityDetail.INACCURATE)

        return Quality(validity=validade, detail=frozenset(detalhes),
                       origin=MetadataOrigin.NATIVE)

    def read(self, address: str) -> RawReading:
        """address = 'device_id/group.variation.index', ex: OUTSTATION-BAT-01/32.7.1"""
        if not self._connected:
            return RawReading(address=address, raw_value=None,
                              error="sessao DNP3 nao estabelecida")
        try:
            device_id, ref = address.split("/", 1)
            grupo, variacao, indice = self._parse_address(ref)
        except ValueError as e:
            return RawReading(address=address, raw_value=None,
                              error=f"endereco DNP3 mal formado: {e}")

        device = self.devices.get(device_id)
        if device is None:
            return RawReading(address=address, raw_value=None,
                              error=f"outstation '{device_id}' desconhecido")

        try:
            bruto, flags, quando = device.poll()
        except ConnectionError as e:
            return RawReading(address=address, raw_value=None,
                              error=f"perda de comunicacao com outstation "
                                    f"'{device_id}' ({e})")

        # Class 0/estatico (Var 5) nao carrega tempo; Class de evento (Var 7/8) carrega.
        tem_timestamp = variacao in (7, 8)

        return RawReading(
            address=address,
            raw_value=bruto,
            native_quality=self._decode_quality(flags),
            native_timestamp=quando if tem_timestamp else None,
        )

    def close(self) -> None:
        self._connected = False
