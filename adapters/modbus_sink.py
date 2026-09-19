"""
Adaptador de DESTINO - Modbus TCP.

Este adaptador existe para demonstrar o caso RICO -> POBRE, que e onde a perda
semantica aparece de forma mais brutal:

    IEC 61850 (valor + qualidade detalhada + timestamp + unidade + modelo)
        |
        v
    Modbus  (um inteiro de 16 bits num endereco. So isso.)

O que acontece com qualidade, timestamp, unidade e significado? Nao ha onde
guardar. O gateway publica o valor e registra explicitamente cada metadado
descartado, com motivo - em vez de deixar sumir em silencio.
"""

from __future__ import annotations

from core.canonical import CanonicalSample
from core.ports import (PublishResult, SinkAdapter, SinkCapabilities,
                        TargetBinding)


class ModbusSink(SinkAdapter):
    """
    Mantem o mapa de registradores exposto ao sistema consumidor do cliente
    (um SCADA legado, por exemplo).

    Nota de honestidade tecnica: o mapa e mantido em memoria neste adaptador.
    Expo-lo como servidor Modbus TCP usa exatamente o mesmo StartTcpServer que
    o simulador em sim/modbus_server.py ja utiliza - e uma chamada, nao uma
    mudanca de arquitetura. O que este adaptador demonstra e a CODIFICACAO
    (float canonico -> inteiro de 16 bits) e a PERDA que ela implica.
    """

    protocol_name = "modbus"
    adapter_version = "1.0"

    def __init__(self, size: int = 200):
        self._registers: dict[int, int] = {}
        self._size = size
        self._ready = False

    @property
    def capabilities(self) -> SinkCapabilities:
        # A declaracao honesta do que Modbus NAO consegue fazer.
        # E daqui que o motor deriva a perda automaticamente.
        return SinkCapabilities(
            supports_quality=False,        # nao existe campo de qualidade
            supports_timestamp=False,      # nao existe campo de tempo
            supports_unit=False,           # unidade e acordo fora de banda
            supports_float=False,          # registradores sao inteiros de 16 bits
            supports_semantic_model=False, # nao ha modelo de informacao
            numeric_bits=16,
            quality_states=0,
        )

    def connect(self) -> None:
        self._ready = True

    def _encode(self, valor_canonico: float, binding: TargetBinding) -> int:
        """
        float canonico -> inteiro de 16 bits, usando a escala do DESTINO.
        84.9 Cel com target_scale 0.1 vira 849 no registrador.
        A fracao alem da resolucao da escala e perdida - por isso o motor
        registra data_type como APPROXIMATED.
        """
        escala = binding.scale if binding.scale else 1.0
        bruto = int(round((valor_canonico - binding.offset) / escala))
        return max(0, min(65535, bruto))

    def publish(self, sample: CanonicalSample, binding: TargetBinding) -> PublishResult:
        if not self._ready:
            return PublishResult(ok=False, detail="mapa de registradores nao iniciado")
        try:
            endereco = int(binding.address)
            offset = endereco - 40001 if endereco >= 40001 else endereco
            codificado = self._encode(float(sample.value), binding)
            self._registers[offset] = codificado

            return PublishResult(
                ok=True,
                detail=f"registrador {binding.address} = {codificado}",
                encoded_payload={
                    "register": binding.address,
                    "raw_written": codificado,
                    "target_scale": binding.scale,
                    # deliberadamente ausentes: quality, timestamp, unit, semantic
                })
        except ValueError:
            return PublishResult(ok=False,
                                 detail=f"endereco de destino invalido: '{binding.address}'")
        except Exception as e:
            return PublishResult(ok=False, detail=f"falha ao escrever em Modbus: {e}")

    def publish_raw(self, payload: dict, binding: TargetBinding) -> PublishResult:
        if not self._ready:
            return PublishResult(ok=False, detail="mapa de registradores nao iniciado")
        try:
            endereco = int(binding.address)
            offset = endereco - 40001 if endereco >= 40001 else endereco
            self._registers[offset] = self._encode(float(payload["value"]), binding)
            return PublishResult(ok=True, detail="reenviado da fila")
        except Exception as e:
            return PublishResult(ok=False, detail=str(e))

    def read_register(self, target_address: str) -> int:
        endereco = int(target_address)
        offset = endereco - 40001 if endereco >= 40001 else endereco
        return self._registers.get(offset, 0)

    def close(self) -> None:
        self._ready = False
