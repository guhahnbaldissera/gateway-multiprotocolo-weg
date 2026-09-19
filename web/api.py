"""
Backend do dashboard - camada FINA sobre o motor que ja existe.

Principio: este arquivo NAO contem regra de negocio. Ele so expoe, via HTTP,
o que core/engine.py, core/store.py e core/registry.py ja fazem. Se alguem
apagar este arquivo, o gateway continua funcionando igual pela CLI e pela demo.

Endpoints:
    GET  /api/status            estado dos 4 protocolos + contadores da fila
    GET  /api/pontos            o de-para completo (cadastro de pontos)
    GET  /api/eventos           ultimos eventos processados
    GET  /api/perdas            perdas semanticas registradas
    GET  /api/auditoria         trilha de auditoria + verificacao da cadeia
    GET  /api/capacidades       matriz de capacidades dos protocolos de destino
    POST /api/traduzir          dispara uma conversao real de um ponto
    POST /api/falha             liga/desliga falhas simuladas (sensor/destino)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, jsonify, request, send_from_directory

from adapters.dnp3_sink import Dnp3Sink
from adapters.dnp3_source import Dnp3Source
from adapters.modbus_sink import ModbusSink
from adapters.modbus_source import ModbusSource
from adapters.opcua_sink import OpcUaSink
from adapters.opcua_source import OpcUaSource
from core.engine import TranslationEngine
from core.ports import PublishResult
from core.registry import MappingRegistry
from core.store import Store
from sim.opcua_sensor import SimulatedOpcUaSensor

RAIZ = Path(__file__).parent.parent
app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"))

# ---------------------------------------------------------------------------
# estado da aplicacao
# ---------------------------------------------------------------------------

class Gateway:
    """Mantem os adaptadores vivos e expoe operacoes para a API."""

    def __init__(self):
        self.store = Store(RAIZ / "gateway.db")
        self.registry, self.erros_config = MappingRegistry.from_yaml(
            RAIZ / "config" / "points.yaml")

        # destinos
        self.opcua_sink = OpcUaSink()
        self.opcua_sink.connect()
        self.modbus_sink = ModbusSink()
        self.modbus_sink.connect()

        # origens
        self.modbus_src = ModbusSource(host="127.0.0.1", port=5020)
        self.modbus_src.connect()

        self.dnp3_src = Dnp3Source()
        self.dnp3_src.connect()

        self.sensor_opcua = SimulatedOpcUaSensor()
        self.sensor_opcua.start()
        self.opcua_src = OpcUaSource(endpoint="opc.tcp://localhost:4841/weg/sensor/")
        self.opcua_src.connect()

        self.mms_src = None
        self.mms_erro = None
        try:
            from adapters.mms_source import MmsSource
            self.mms_src = MmsSource(host="localhost", port=102)
            self.mms_src.connect()
        except Exception as e:
            self.mms_erro = str(e)

        sources = {"modbus": self.modbus_src, "dnp3": self.dnp3_src,
                   "opcua": self.opcua_src}
        if self.mms_src:
            sources["iec61850-mms"] = self.mms_src

        self.sinks = {"opcua": self.opcua_sink, "modbus": self.modbus_sink}
        self.engine = TranslationEngine(
            self.registry, self.store, sources, self.sinks,
            allowed_devices={"IED-MODBUS-01", "IED-61850-01",
                             "OUTSTATION-BAT-01", "OPCUA-SENSOR-01"})

        self.destino_derrubado = False
        self._sink_real = self.opcua_sink

    # -- simulacao de falhas ------------------------------------------------

    def derrubar_destino(self, derrubar: bool) -> None:
        """Simula o sistema consumidor fora do ar (cenario store-and-forward)."""
        self.destino_derrubado = derrubar
        if derrubar:
            gw = self

            class DestinoOffline:
                protocol_name = "opcua"
                adapter_version = "offline"
                capabilities = gw._sink_real.capabilities

                def publish(self, sample, binding):
                    return PublishResult(ok=False,
                                         detail="destino inacessivel (simulado)")

                def publish_raw(self, payload, binding):
                    return PublishResult(ok=False,
                                         detail="destino inacessivel (simulado)")

            self.engine.sinks["opcua"] = DestinoOffline()
        else:
            self.engine.sinks["opcua"] = self._sink_real

    def falha_sensor(self, protocolo: str, ligar: bool) -> str:
        """Simula sensor fora do ar (falta de sensor)."""
        if protocolo == "dnp3":
            self.dnp3_src.devices["OUTSTATION-BAT-01"].set_offline(ligar)
            return "outstation DNP3 " + ("offline" if ligar else "online")
        if protocolo == "opcua":
            self.sensor_opcua.set_fault(ligar)
            return "sensor OPC UA " + ("com StatusCode Bad" if ligar else "normal")
        return f"simulacao nao disponivel para '{protocolo}'"

    def falha_mecanica(self, protocolo: str, ligar: bool) -> str:
        """Simula defeito mecanico (leitura implausivel)."""
        if protocolo == "dnp3":
            self.dnp3_src.devices["OUTSTATION-BAT-01"].set_fault(ligar)
            return "outstation DNP3 " + ("com defeito" if ligar else "normal")
        if protocolo == "opcua":
            self.sensor_opcua.set_fault(ligar)
            return "sensor OPC UA " + ("com defeito" if ligar else "normal")
        return f"simulacao nao disponivel para '{protocolo}'"

    def status_protocolos(self) -> list[dict]:
        def entrada(nome, rotulo, adaptador, detalhe=""):
            conectado = bool(adaptador) and getattr(adaptador, "connected", False)
            return {"protocolo": nome, "rotulo": rotulo,
                    "conectado": conectado, "detalhe": detalhe,
                    "papel": "origem"}

        itens = [
            entrada("modbus", "Modbus TCP", self.modbus_src,
                    "IED-MODBUS-01 @ 127.0.0.1:5020"),
            entrada("iec61850-mms", "IEC 61850 MMS", self.mms_src,
                    self.mms_erro or "IED-61850-01 @ localhost:102"),
            entrada("dnp3", "DNP3", self.dnp3_src,
                    "OUTSTATION-BAT-01 (fonte simulada)"),
            entrada("opcua", "OPC UA", self.opcua_src,
                    "OPCUA-SENSOR-01 @ localhost:4841"),
        ]
        itens.append({"protocolo": "opcua", "rotulo": "OPC UA (destino)",
                      "conectado": not self.destino_derrubado,
                      "detalhe": "servidor local :4840", "papel": "destino"})
        itens.append({"protocolo": "modbus", "rotulo": "Modbus (destino)",
                      "conectado": True,
                      "detalhe": "mapa de registradores", "papel": "destino"})
        return itens


gateway: Gateway | None = None


def get_gateway() -> Gateway:
    global gateway
    if gateway is None:
        gateway = Gateway()
    return gateway


# ---------------------------------------------------------------------------
# rotas
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def api_status():
    gw = get_gateway()
    return jsonify({
        "protocolos": gw.status_protocolos(),
        "fila": gw.store.counts(),
        "destino_derrubado": gw.destino_derrubado,
        "mapping_version": gw.registry.version,
        "pontos_validos": len(gw.registry),
        "pontos_rejeitados": len(gw.erros_config),
    })


@app.route("/api/pontos")
def api_pontos():
    gw = get_gateway()
    return jsonify([{
        "point_id": p.point_id,
        "source_protocol": p.source_protocol,
        "source_device": p.source_device,
        "source_address": p.source_address,
        "asset_id": p.asset_id,
        "asset_type": p.asset_type,
        "measurement": p.measurement,
        "iec61850_hint": p.iec61850_hint,
        "data_type": p.data_type.value,
        "unit": p.unit,
        "scale": p.scale,
        "offset": p.offset,
        "min_value": p.min_value,
        "max_value": p.max_value,
        "target_protocol": p.target_protocol,
        "target_address": p.target_address,
        "target_scale": p.target_scale,
    } for p in gw.registry.all()])


@app.route("/api/eventos")
def api_eventos():
    gw = get_gateway()
    limite = int(request.args.get("limite", 40))
    linhas = gw.store.conn.execute(
        "SELECT event_id, point_id, state, attempts, last_error, payload, created_at "
        "FROM events ORDER BY rowid DESC LIMIT ?", (limite,))
    import json
    saida = []
    for r in linhas:
        payload = json.loads(r["payload"])
        saida.append({
            "event_id": r["event_id"],
            "point_id": r["point_id"],
            "state": r["state"],
            "attempts": r["attempts"],
            "last_error": r["last_error"],
            "created_at": r["created_at"],
            "asset_id": payload.get("asset_id"),
            "measurement": payload.get("measurement"),
            "raw_value": payload.get("raw_value"),
            "value": payload.get("value"),
            "unit": payload.get("unit"),
            "quality": payload.get("quality"),
            "quality_origin": payload.get("quality_origin"),
            "timestamp_origin": payload.get("timestamp_origin"),
            "source_protocol": payload.get("source_protocol"),
        })
    return jsonify(saida)


@app.route("/api/perdas")
def api_perdas():
    gw = get_gateway()
    resumo = [dict(r) for r in gw.store.loss_summary()]
    recentes = [dict(r) for r in gw.store.conn.execute(
        "SELECT * FROM losses ORDER BY id DESC LIMIT 40")]
    return jsonify({"resumo": resumo, "recentes": recentes})


@app.route("/api/auditoria")
def api_auditoria():
    gw = get_gateway()
    integro, quebra = gw.store.verify_audit_chain()
    linhas = [dict(r) for r in gw.store.conn.execute(
        "SELECT seq, action, detail, entry_hash, created_at FROM audit "
        "ORDER BY seq DESC LIMIT 30")]
    return jsonify({"integra": integro, "quebra_em": quebra, "entradas": linhas})


@app.route("/api/capacidades")
def api_capacidades():
    def cap(nome, c, implementado):
        return {"protocolo": nome, "implementado": implementado,
                "qualidade": c.supports_quality, "timestamp": c.supports_timestamp,
                "unidade": c.supports_unit, "semantica": c.supports_semantic_model,
                "float": c.supports_float, "bits": c.numeric_bits,
                "estados_qualidade": c.quality_states}

    return jsonify([
        cap("OPC UA", OpcUaSink().capabilities, True),
        cap("Modbus", ModbusSink().capabilities, True),
        cap("DNP3", Dnp3Sink().capabilities, False),
    ])


@app.route("/api/traduzir", methods=["POST"])
def api_traduzir():
    """Dispara uma conversao REAL - mesma funcao que a demo e a CLI usam."""
    gw = get_gateway()
    point_id = (request.json or {}).get("point_id")
    p = gw.registry.get(point_id)
    if p is None:
        return jsonify({"erro": f"ponto '{point_id}' nao encontrado"}), 404

    r = gw.engine.process_point(p)
    s = r.get("sample")

    return jsonify({
        "point_id": point_id,
        "state": r["state"],
        "detail": r["detail"],
        "origem": {
            "protocolo": p.source_protocol,
            "dispositivo": p.source_device,
            "endereco": p.source_address,
            "valor_bruto": s.raw_value if s else None,
        },
        "canonico": None if not s else {
            "significado": {
                "asset_id": s.semantic.asset_id,
                "asset_type": s.semantic.asset_type,
                "measurement": s.semantic.measurement,
                "iec61850_hint": s.semantic.iec61850_hint,
            },
            "tipo": s.data_type.value,
            "unidade": s.unit,
            "qualidade": {"valor": s.quality.describe(),
                          "origem": s.quality.origin.value,
                          "nativa": s.quality.is_native},
            "timestamp": {"valor": s.timestamp.value.isoformat(),
                          "origem": s.timestamp.origin.value,
                          "aproximado": s.timestamp.is_approximate},
            "valor": s.value,
            "escala": s.scale,
            "offset": s.offset,
            "event_id": s.event_id,
        },
        "destino": {
            "protocolo": p.target_protocol,
            "endereco": p.target_address,
            "escala": p.target_scale,
        },
        "perdas": [l.to_dict() for l in r["losses"]],
    })


@app.route("/api/flush", methods=["POST"])
def api_flush():
    gw = get_gateway()
    return jsonify(gw.engine.flush_queue())


@app.route("/api/falha", methods=["POST"])
def api_falha():
    gw = get_gateway()
    body = request.json or {}
    tipo = body.get("tipo")
    ligar = bool(body.get("ligar"))

    if tipo == "destino":
        gw.derrubar_destino(ligar)
        return jsonify({"ok": True,
                        "mensagem": "destino OPC UA " +
                                    ("derrubado" if ligar else "religado")})
    if tipo == "sensor":
        return jsonify({"ok": True,
                        "mensagem": gw.falha_sensor(body.get("protocolo"), ligar)})
    if tipo == "mecanica":
        return jsonify({"ok": True,
                        "mensagem": gw.falha_mecanica(body.get("protocolo"), ligar)})
    return jsonify({"erro": f"tipo de falha desconhecido: '{tipo}'"}), 400


if __name__ == "__main__":
    get_gateway()
    print("\nDashboard em http://localhost:8080\n")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
