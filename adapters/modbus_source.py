"""
Adaptador de ORIGEM - Modbus TCP.

Toda dependencia de pymodbus esta confinada neste arquivo.
O nucleo nunca ve um objeto de pymodbus: recebe apenas RawReading.

Modbus e o protocolo semanticamente POBRE do nosso par de demonstracao:
    - nao carrega tipo
    - nao carrega unidade
    - nao carrega escala
    - nao carrega qualidade
    - nao carrega timestamp
Ele entrega apenas "endereco -> inteiro de 16 bits".
Por isso native_quality e native_timestamp voltam None: a ausencia e explicita,
e o nucleo registra que qualquer metadado desses foi SINTETIZADO.
"""

from __future__ import annotations

from pymodbus.client import ModbusTcpClient

from core.ports import RawReading, SourceAdapter


class ModbusSource(SourceAdapter):
    protocol_name = "modbus"
    adapter_version = "1.0"

    def __init__(self, host: str = "127.0.0.1", port: int = 5020, device_id: int = 1):
        self.host = host
        self.port = port
        self.device_id = device_id
        self._client: ModbusTcpClient | None = None

    def connect(self) -> None:
        self._client = ModbusTcpClient(self.host, port=self.port, timeout=2)
        self._client.connect()

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.connected)

    def read(self, address: str) -> RawReading:
        """address no formato '40001' (holding register) ou '30001' (input register)."""
        if self._client is None:
            return RawReading(address=address, raw_value=None,
                              error="cliente Modbus nao inicializado")

        try:
            numero = int(address)
        except ValueError:
            return RawReading(address=address, raw_value=None,
                              error=f"endereco Modbus invalido: '{address}'")

        # convencao classica: 4xxxx = holding, 3xxxx = input
        if numero >= 40001:
            offset, leitura = numero - 40001, "holding"
        elif numero >= 30001:
            offset, leitura = numero - 30001, "input"
        else:
            offset, leitura = numero, "holding"

        try:
            if not self.connected:
                self.connect()
            if leitura == "holding":
                rr = self._client.read_holding_registers(offset, count=1,
                                                         device_id=self.device_id)
            else:
                rr = self._client.read_input_registers(offset, count=1,
                                                       device_id=self.device_id)
            if rr.isError():
                return RawReading(address=address, raw_value=None,
                                  error=f"erro Modbus na leitura de {address}: {rr}")

            return RawReading(
                address=address,
                raw_value=rr.registers[0],
                native_quality=None,     # Modbus nao tem qualidade
                native_timestamp=None,   # Modbus nao tem timestamp
            )
        except Exception as e:
            return RawReading(address=address, raw_value=None,
                              error=f"perda de comunicacao com {self.host}:{self.port} ({e})")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
