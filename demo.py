"""
Demonstracao ponta a ponta do Gateway Multiprotocolo.

Cenarios (cada um responde a um requisito do desafio WEG):

    1. Fluxo POBRE -> RICO    Modbus -> OPC UA     (enriquecimento semantico)
    2. Fluxo RICO  -> POBRE   IEC 61850 -> Modbus  (perda semantica explicita)
    3. Configuracao invalida                       (ponto rejeitado no carregamento)
    4. Valor fora de faixa                         (quarentena com justificativa)
    5. Destino fora do ar + reenvio                (nada se perde em silencio)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from adapters.dnp3_sink import Dnp3Sink
from adapters.dnp3_source import Dnp3Source
from adapters.modbus_sink import ModbusSink
from adapters.modbus_source import ModbusSource
from adapters.opcua_sink import OpcUaSink
from adapters.opcua_source import OpcUaSource
from core.engine import TranslationEngine
from core.loss import loss_matrix
from core.registry import MappingRegistry
from core.store import EventState, Store
from sim.opcua_sensor import SimulatedOpcUaSensor

RAIZ = Path(__file__).parent
AZUL, VERDE, AMAR, VERM, CINZA, FIM = "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[0m"


def titulo(n, texto):
    print(f"\n{AZUL}{'=' * 78}\n  CENARIO {n} - {texto}\n{'=' * 78}{FIM}")


def linha(rotulo, valor, cor=""):
    print(f"   {rotulo:<28} {cor}{valor}{FIM}")


def mostrar_perdas(perdas):
    if not perdas:
        print(f"   {VERDE}Nenhuma perda semantica: o destino representa todos os metadados.{FIM}")
        return
    print(f"\n   {AMAR}PERDAS SEMANTICAS REGISTRADAS ({len(perdas)}):{FIM}")
    for p in perdas:
        print(f"   {AMAR}-> {p.human()}{FIM}")


def main():
    store = Store(RAIZ / "gateway.db")
    store.audit("startup", "demonstracao iniciada")

    # ------------------------------------------------------------------
    # CENARIO 3 (feito primeiro: acontece no carregamento da configuracao)
    # ------------------------------------------------------------------
    titulo(3, "CONFIGURACAO INVALIDA (rejeitada no carregamento)")
    registry_ruim, erros = MappingRegistry.from_yaml(RAIZ / "config" / "points_invalid.yaml")
    print(f"   Pontos validos carregados: {len(registry_ruim)}")
    for e in erros:
        print(f"   {VERM}REJEITADO -> {e}{FIM}")
        store.audit("invalid_mapping", str(e))
    print(f"\n   {CINZA}O gateway nao sobe com mapeamento quebrado silenciosamente:{FIM}")
    print(f"   {CINZA}isola o ponto ruim, registra o motivo e opera os validos.{FIM}")

    # ------------------------------------------------------------------
    registry, erros = MappingRegistry.from_yaml(RAIZ / "config" / "points.yaml")
    print(f"\n{VERDE}De-para carregado: {len(registry)} pontos "
          f"(versao {registry.version}){FIM}")

    # --- adaptadores de destino ---
    opcua = OpcUaSink()
    opcua.connect()
    modbus_sink = ModbusSink()
    modbus_sink.connect()
    sinks = {"opcua": opcua, "modbus": modbus_sink}

    # --- adaptadores de origem: os 4 protocolos do desafio ---
    modbus_src = ModbusSource(host="127.0.0.1", port=5020)
    modbus_src.connect()

    dnp3_src = Dnp3Source()
    dnp3_src.connect()
    print(f"{AMAR}DNP3: fonte SIMULADA (OpenDNP3 arquivado desde 2022-05-18){FIM}")

    sensor_opcua = SimulatedOpcUaSensor()
    sensor_opcua.start()
    opcua_src = OpcUaSource(endpoint="opc.tcp://localhost:4841/weg/sensor/")
    opcua_src.connect()
    print(f"{VERDE}Sensor OPC UA nativo simulado em localhost:4841{FIM}")

    sources = {"modbus": modbus_src, "dnp3": dnp3_src, "opcua": opcua_src}

    # MMS so entra se o IED simulado estiver no ar
    try:
        from adapters.mms_source import MmsSource
        mms = MmsSource(host="localhost", port=102)
        mms.connect()
        sources["iec61850-mms"] = mms
        print(f"{VERDE}IED IEC 61850 conectado em localhost:102{FIM}")
    except Exception as e:
        mms = None
        print(f"{VERM}IED 61850 indisponivel ({e}){FIM}")

    engine = TranslationEngine(registry, store, sources, sinks, allowed_devices={
        "IED-MODBUS-01", "IED-61850-01", "OUTSTATION-BAT-01", "OPCUA-SENSOR-01"})

    # ------------------------------------------------------------------
    titulo(1, "POBRE -> RICO : Modbus -> OPC UA (enriquecimento)")
    p = registry.get("TR01_OIL_TEMP_MODBUS")
    r = engine.process_point(p)
    s = r["sample"]
    if s:
        linha("Origem", f"{p.source_protocol} registrador {p.source_address}")
        linha("Valor cru (16 bits)", s.raw_value, CINZA)
        linha("Escala / offset", f"x{s.scale}  {s.offset:+}")
        linha("Valor normalizado", f"{s.value} {s.unit}", VERDE)
        linha("Significado", f"{s.semantic.asset_type} / {s.semantic.measurement}")
        linha("Ponte IEC 61850", s.semantic.iec61850_hint)
        linha("Qualidade", f"{s.quality.describe()}  (origem: {s.quality.origin.value})",
              AMAR if not s.quality.is_native else VERDE)
        linha("Timestamp", f"{s.timestamp.value.isoformat()}", "")
        linha("Timestamp aproximado?", s.timestamp.is_approximate,
              AMAR if s.timestamp.is_approximate else VERDE)
        linha("Estado final", r["state"], VERDE if r["state"] == "delivered" else AMAR)
        mostrar_perdas(r["losses"])

    # ------------------------------------------------------------------
    titulo("Exemplo Pratico 2 (oficial)", "DNP3 -> OPC UA (banco de baterias)")
    p = registry.get("BAT01_VOLTAGE_DNP3")
    r = engine.process_point(p)
    s = r["sample"]
    if s:
        linha("Origem", f"{p.source_protocol} (simulado) {p.source_address}")
        linha("Valor cru (DNP3, x100)", s.raw_value, CINZA)
        linha("Escala / offset", f"x{s.scale}  {s.offset:+}")
        linha("Valor normalizado", f"{s.value} {s.unit}", VERDE)
        linha("Qualidade NATIVA (flags DNP3)", f"{s.quality.describe()} "
                                                f"(origem: {s.quality.origin.value})", VERDE)
        linha("Timestamp NATIVO", s.timestamp.value.isoformat()
              if not s.timestamp.is_approximate else "aproximado", VERDE)
        linha("Estado final", r["state"], VERDE if r["state"] == "delivered" else AMAR)
        mostrar_perdas(r["losses"])
        print(f"\n   {CINZA}DNP3 e mais pobre que 61850 (flags de 1 byte vs. bit-string),{FIM}")
        print(f"   {CINZA}mas ainda assim mais rico que Modbus - por isso poucas/nenhuma perda.{FIM}")

    titulo("Fluxo 4", "OPC UA -> Modbus (origem OPC UA nativa)")
    p = registry.get("SENSOR_AMBIENT_OPCUA")
    r = engine.process_point(p)
    s = r["sample"]
    if s:
        linha("Origem", f"{p.source_protocol} (sensor nativo simulado)")
        linha("Valor", f"{s.value} {s.unit}", VERDE)
        linha("Qualidade NATIVA (StatusCode)", f"{s.quality.describe()} "
                                                f"(origem: {s.quality.origin.value})", VERDE)
        linha("Estado final", r["state"], VERDE if r["state"] == "delivered" else AMAR)
        mostrar_perdas(r["losses"])

    # ------------------------------------------------------------------
    if mms:
        titulo(2, "RICO -> POBRE : IEC 61850 MMS -> Modbus (perda semantica)")
        p = registry.get("TR02_OIL_TEMP_MMS")
        r = engine.process_point(p)
        s = r["sample"]
        if s:
            linha("Origem", f"{p.source_protocol}")
            linha("Referencia 61850", p.source_address, CINZA)
            linha("Valor lido", f"{s.value} {s.unit}", VERDE)
            linha("Qualidade NATIVA", f"{s.quality.describe()} "
                                      f"(origem: {s.quality.origin.value})", VERDE)
            linha("Timestamp NATIVO", s.timestamp.value.isoformat(), VERDE)
            linha("Destino", f"Modbus registrador {p.target_address}")
            linha("Estado final", r["state"], VERDE if r["state"] == "delivered" else AMAR)
            try:
                linha("Registrador escrito", modbus_sink.read_register(p.target_address), VERDE)
            except Exception:
                pass
            mostrar_perdas(r["losses"])
            print(f"\n   {CINZA}O valor chegou. A qualidade e o instante de aquisicao{FIM}")
            print(f"   {CINZA}nao chegaram - e isso ficou REGISTRADO, nao sumiu.{FIM}")

        titulo("2b", "RICO -> RICO : IEC 61850 MMS -> OPC UA (menor perda)")
        p = registry.get("TR02_AMBIENT_TEMP_MMS")
        r = engine.process_point(p)
        if r["sample"]:
            linha("Valor", f"{r['sample'].value} {r['sample'].unit}", VERDE)
            linha("Estado final", r["state"], VERDE)
            mostrar_perdas(r["losses"])

    # ------------------------------------------------------------------
    titulo(4, "VALOR FORA DE FAIXA (quarentena, nao descarte)")
    p = registry.get("TR01_OIL_TEMP_FORA_FAIXA")
    r = engine.process_point(p)
    linha("Estado final", r["state"], VERM)
    linha("Motivo", r["detail"], VERM)
    print(f"\n   {CINZA}O evento nao foi jogado fora: esta em quarentena, com{FIM}")
    print(f"   {CINZA}justificativa, disponivel para auditoria e reprocessamento.{FIM}")

    # ------------------------------------------------------------------
    titulo(5, "DESTINO FORA DO AR + REENVIO (store-and-forward)")

    class DestinoOffline(OpcUaSink):
        """Simula o sistema de destino indisponivel."""
        def publish(self, sample, binding):
            from core.ports import PublishResult
            return PublishResult(ok=False, detail="destino inacessivel (simulado)")

    offline = DestinoOffline.__new__(DestinoOffline)
    offline.__dict__.update(opcua.__dict__)
    engine.sinks["opcua"] = offline

    print(f"   {VERM}Destino OPC UA derrubado.{FIM} Gerando eventos...")
    for _ in range(5):
        engine.process_point(registry.get("TR01_OIL_TEMP_MODBUS"))

    c = store.counts()
    linha("pending + retry", c["pending"] + c["retry"], AMAR)
    linha("delivered", c["delivered"], VERDE)
    print(f"   {CINZA}Nenhum evento sumiu - todos estao em estado verificavel.{FIM}")

    print(f"\n   {VERDE}Religando o destino...{FIM}")
    engine.sinks["opcua"] = opcua
    res = engine.flush_queue()
    linha("Reenviados com sucesso", res["delivered"], VERDE)
    c = store.counts()
    linha("pending + retry apos flush", c["pending"] + c["retry"], VERDE)
    linha("delivered", c["delivered"], VERDE)

    # ------------------------------------------------------------------
    titulo(6, "OS 4 PROTOCOLOS RODANDO - AVISO DE FALHA (sensor ausente / defeito mecanico)")

    print(f"\n   {CINZA}Duas classes de falha, testadas nos 4 protocolos simultaneamente:{FIM}")
    print(f"   {CINZA}(a) FALTA DE SENSOR      -> falha de comunicacao, sem leitura possivel{FIM}")
    print(f"   {CINZA}(b) PROBLEMA MECANICO    -> sensor responde, mas com valor/qualidade "
          f"implausivel{FIM}\n")

    print(f"   {AMAR}--- (a) FALTA DE SENSOR ---{FIM}")

    def testar_comm_error(nome_protocolo, fonte, endereco):
        r = fonte.read(endereco)
        if r.ok:
            print(f"   {VERM}[{nome_protocolo}] inesperado: leitura funcionou{FIM}")
        else:
            print(f"   {VERDE}[{nome_protocolo}]{FIM} sensor indisponivel -> "
                  f"{AMAR}aviso: \"{r.error}\"{FIM}")
            store.audit("comm_error_demo", f"{nome_protocolo}: {r.error}")

    # Modbus: aponta para porta onde nao ha ninguem escutando
    modbus_offline = ModbusSource(host="127.0.0.1", port=5099)
    modbus_offline.connect()
    testar_comm_error("MODBUS", modbus_offline, "40001")
    modbus_offline.close()

    # MMS: aponta para porta sem servidor
    if mms:
        from adapters.mms_source import MmsSource
        try:
            mms_offline = MmsSource(host="localhost", port=199)
            mms_offline.connect()
        except Exception as e:
            print(f"   {VERDE}[IEC 61850 MMS]{FIM} sensor indisponivel -> "
                  f"{AMAR}aviso: \"{e}\"{FIM}")
            store.audit("comm_error_demo", f"iec61850-mms: {e}")

    # DNP3: outstation simulado colocado offline sob comando
    dnp3_src.devices["OUTSTATION-BAT-01"].set_offline(True)
    testar_comm_error("DNP3", dnp3_src, "OUTSTATION-BAT-01/32.7.1")
    dnp3_src.devices["OUTSTATION-BAT-01"].set_offline(False)

    # OPC UA: aponta para porta onde nao ha servidor
    opcua_offline = OpcUaSource(endpoint="opc.tcp://localhost:4899/nada/")
    testar_comm_error("OPC UA", opcua_offline, "ns=2;s=AmbientTemperature")

    print(f"\n   {AMAR}--- (b) PROBLEMA MECANICO (sensor responde, dado implausivel) ---{FIM}")

    def testar_fault(nome_protocolo, point_id):
        p = registry.get(point_id)
        r = engine.process_point(p)
        cor = VERM if r["state"] == "quarantine" else AMAR
        print(f"   {VERDE}[{nome_protocolo}]{FIM} estado: {cor}{r['state']}{FIM}  "
              f"motivo: {cor}{r['detail']}{FIM}")

    testar_fault("MODBUS", "TR01_OIL_TEMP_FORA_FAIXA")
    testar_fault("IEC 61850 MMS", "TR02_OIL_TEMP_MMS_FAIXA_ESTREITA")

    dnp3_src.devices["OUTSTATION-BAT-01"].set_fault(True)
    testar_fault("DNP3", "BAT01_VOLTAGE_DNP3")
    dnp3_src.devices["OUTSTATION-BAT-01"].set_fault(False)

    sensor_opcua.set_fault(True)
    testar_fault("OPC UA", "SENSOR_AMBIENT_OPCUA")
    sensor_opcua.set_fault(False)
    print(f"   {CINZA}(OPC UA: o cliente rejeita a leitura na propria camada de{FIM}")
    print(f"   {CINZA}protocolo quando o StatusCode do servidor e Bad - a falha e{FIM}")
    print(f"   {CINZA}pega um passo ANTES do nosso motor de quarentena, no proprio{FIM}")
    print(f"   {CINZA}protocolo. E o unico dos 4 onde isso acontece, porque e o{FIM}")
    print(f"   {CINZA}unico com qualidade nativa rica o bastante pra carregar 'Bad'.){FIM}")

    print(f"\n   {CINZA}Em nenhum caso o sistema travou ou perdeu o evento em silencio:{FIM}")
    print(f"   {CINZA}falta de sensor e defeito mecanico ficam sempre visiveis, com motivo.{FIM}")

    # ------------------------------------------------------------------
    print(f"\n{AZUL}{'=' * 78}\n  MATRIZ DE CAPACIDADES (base da analise automatica de perda)\n"
          f"{'=' * 78}{FIM}")
    print(loss_matrix({
        "OPC UA": opcua.capabilities,
        "Modbus": modbus_sink.capabilities,
        "DNP3 (nao implementado)": Dnp3Sink().capabilities,
    }))

    print(f"\n{AZUL}{'=' * 78}\n  RESUMO DE PERDAS ACUMULADAS\n{'=' * 78}{FIM}")
    for row in store.loss_summary():
        print(f"   {row['loss_type']:<14} {row['field']:<18} x{row['total']}")

    integro, quebra = store.verify_audit_chain()
    print(f"\n   Trilha de auditoria integra: "
          f"{VERDE + 'SIM' + FIM if integro else VERM + f'NAO (seq {quebra})' + FIM}")

    for a in sources.values():
        a.close()
    opcua.close()
    sensor_opcua.stop()
    store.close()


if __name__ == "__main__":
    main()
