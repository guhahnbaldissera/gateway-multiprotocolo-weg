"""
Sensor OPC UA simulado - representa um IED moderno que fala OPC UA nativamente
(ex.: monitor de qualidade de energia de nova geracao).

Diferente do simulador Modbus (processo separado), este roda embutido no mesmo
processo Python que o consome - simplifica a demonstracao sem abrir mao de
trafego OPC UA real na rede local (usa o mesmo asyncua.sync.Server real).
"""

from __future__ import annotations

import random

from asyncua import ua
from asyncua.sync import Server


class SimulatedOpcUaSensor:
    """
    Controlavel para os cenarios de demonstracao:
        set_fault(True) -> proxima leitura sai com StatusCode Bad + valor implausivel
        stop()          -> simula sensor fora do ar (conexao recusada)
    """

    def __init__(self, endpoint: str = "opc.tcp://0.0.0.0:4841/weg/sensor/"):
        self.endpoint = endpoint
        self._server: Server | None = None
        self._node = None
        self._fault = False

    def start(self) -> None:
        self._server = Server()
        self._server.set_endpoint(self.endpoint)
        self._server.set_server_name("Sensor OPC UA nativo (simulado)")
        idx = self._server.register_namespace("http://weg.hackathon/sensor")
        pasta = self._server.nodes.objects.add_folder(idx, "Sensores")
        # NodeId string fixo e previsivel (o padrao seria numerico e mudaria
        # a cada execucao) - assim o de-para no YAML pode referenciar um
        # endereco estavel, como aconteceria com um sensor OPC UA real.
        node_id = ua.NodeId("AmbientTemperature", idx, ua.NodeIdType.String)
        self._node = pasta.add_variable(node_id, "AmbientTemperature", 25.0)
        self.node_id_str = node_id.to_string()
        self._server.start()
        self._tick()

    def set_fault(self, valor: bool) -> None:
        self._fault = valor
        self._tick()

    def _tick(self) -> None:
        if self._node is None:
            return
        if self._fault:
            dv = ua.DataValue(
                Value=ua.Variant(9999.0, ua.VariantType.Double),
                StatusCode=ua.StatusCode(ua.StatusCodes.Bad),
            )
        else:
            valor = 25.0 + random.uniform(-2.0, 2.0)
            dv = ua.DataValue(
                Value=ua.Variant(valor, ua.VariantType.Double),
                StatusCode=ua.StatusCode(ua.StatusCodes.Good),
            )
        self._node.set_value(dv)

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None
