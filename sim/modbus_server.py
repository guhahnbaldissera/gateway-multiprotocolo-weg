"""
Simulador de IED Modbus - representa um sensor de transformador.

Substitui o hardware real (WEG Transformer Industrial Scan) para a demonstracao.
Os valores gravados sao inteiros crus de 16 bits, exatamente como um dispositivo
Modbus real entrega: sem tipo, sem unidade, sem escala, sem qualidade, sem tempo.

Mapa de registradores (holding):
    40001 -> 253    temperatura do topo do oleo   (escala 0.1 -> 25.3 Cel)
    40002 -> 846    corrente secundaria fase A    (escala 0.5 -> 423.0 A)
    40003 -> 9999   sensor B com leitura absurda  (dispara quarentena)
"""

import random
import threading
import time

from pymodbus.datastore import (ModbusDeviceContext, ModbusSequentialDataBlock,
                                ModbusServerContext)
from pymodbus.server import StartTcpServer

HOST = "127.0.0.1"
PORT = 5020

_valores = [0] * 100
_valores[0] = 253     # 40001
_valores[1] = 846     # 40002
_valores[2] = 9999    # 40003 - fora de faixa de proposito

_block = ModbusSequentialDataBlock(1, _valores)
_device = ModbusDeviceContext(hr=_block, ir=_block)
context = ModbusServerContext(devices=_device, single=True)


def _variar():
    """Faz a temperatura oscilar, como um sensor real."""
    while True:
        time.sleep(2)
        atual = _device.getValues(3, 0, 1)[0]
        novo = max(200, min(320, atual + random.randint(-3, 3)))
        _device.setValues(3, 0, [novo])


if __name__ == "__main__":
    threading.Thread(target=_variar, daemon=True).start()
    print(f"[SIM] IED Modbus simulado em {HOST}:{PORT}")
    print("[SIM] 40001=temp oleo  40002=corrente L1  40003=sensor fora de faixa")
    StartTcpServer(context=context, address=(HOST, PORT))
