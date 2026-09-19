"""
Interface de linha de comando do Gateway Multiprotocolo.

REQUISITO WEG (Interface):
"cadastrar um novo ponto, alterar o endereco de origem ou de destino mapeados,
definir os protocolos de origem e destino, selecionar os tipos de dados
envolvidos, configurar fatores de escala e offset e definir unidades de
engenharia" + "carregar, validar e consultar o mapeamento de enderecos".

Nenhuma alteracao aqui exige recompilar ou reiniciar a logica de traducao:
o de-para e dado, nao codigo.

Uso:
    python cli.py listar
    python cli.py ver TR01_OIL_TEMP_MODBUS
    python cli.py validar
    python cli.py capacidades
    python cli.py perdas
    python cli.py fila
    python cli.py cadastrar --point-id TR03_TEMP --source-protocol modbus \\
        --source-device IED-MODBUS-02 --source-address 40020 \\
        --asset-id TR-03 --asset-type power_transformer \\
        --measurement oil_top_temperature --unit Cel --scale 0.1 \\
        --target-protocol opcua --target-address TR-03.OilTemp
    python cli.py alterar TR03_TEMP --scale 0.5 --target-address TR-03.Temp
    python cli.py remover TR03_TEMP
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from adapters.dnp3_sink import Dnp3Sink
from adapters.modbus_sink import ModbusSink
from adapters.opcua_sink import OpcUaSink
from core.loss import loss_matrix
from core.registry import InvalidMappingError, MappingRegistry

RAIZ = Path(__file__).parent
CONFIG = RAIZ / "config" / "points.yaml"

VERDE, VERM, AMAR, CINZA, FIM = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"


# ---------------------------------------------------------------------------
# helpers de persistencia (o de-para vive no YAML)
# ---------------------------------------------------------------------------

def _ruamel():
    """
    Round-trip que PRESERVA os comentarios do YAML.

    Importa: o points.yaml documenta cada fluxo de conversao e e parte da
    evidencia entregue. Um dump comum apagaria esses comentarios a cada
    cadastro feito pela CLI.
    """
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 100
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def carregar_bruto(caminho: Path) -> dict:
    with caminho.open(encoding="utf-8") as f:
        dados = _ruamel().load(f)
    return dados if dados else {"points": []}


def salvar_bruto(caminho: Path, dados: dict) -> None:
    with caminho.open("w", encoding="utf-8") as f:
        _ruamel().dump(dados, f)


def validar_antes_de_salvar(dados: dict) -> list[str]:
    """Roda a mesma validacao do runtime antes de gravar. Nada invalido e persistido."""
    erros = []
    for item in dados.get("points", []):
        try:
            MappingRegistry._build_point(item)
        except InvalidMappingError as e:
            erros.append(str(e))
    return erros


# ---------------------------------------------------------------------------
# comandos
# ---------------------------------------------------------------------------

def cmd_listar(args):
    reg, erros = MappingRegistry.from_yaml(CONFIG)
    print(f"\nDe-para versao {reg.version} - {len(reg)} pontos validos")
    if erros:
        print(f"{VERM}{len(erros)} pontos rejeitados na validacao{FIM}")
    print(f"\n{'POINT_ID':<28} {'ORIGEM':<16} {'ENDERECO':<38} {'DESTINO':<10} {'ENDERECO DEST':<28} {'UNID':<6}")
    print("-" * 132)
    for p in reg.all():
        print(f"{p.point_id:<28} {p.source_protocol:<16} {p.source_address:<38} "
              f"{p.target_protocol:<10} {p.target_address:<28} {p.unit:<6}")
    for e in erros:
        print(f"{VERM}REJEITADO: {e}{FIM}")


def cmd_ver(args):
    reg, _ = MappingRegistry.from_yaml(CONFIG)
    p = reg.get(args.point_id)
    if not p:
        print(f"{VERM}ponto '{args.point_id}' nao encontrado{FIM}")
        return 1
    print(f"\n{VERDE}{p.point_id}{FIM}")
    print(f"\n  ORIGEM")
    print(f"    protocolo          {p.source_protocol}")
    print(f"    dispositivo        {p.source_device}")
    print(f"    endereco           {p.source_address}")
    print(f"\n  SIGNIFICADO (pilar 1)")
    print(f"    ativo              {p.asset_id} ({p.asset_type})")
    print(f"    grandeza           {p.measurement}")
    print(f"    referencia 61850   {p.iec61850_hint or '-'}")
    print(f"\n  NORMALIZACAO (pilares 2 e 3)")
    print(f"    tipo               {p.data_type.value}")
    print(f"    unidade            {p.unit}")
    print(f"    escala / offset    x{p.scale}  {p.offset:+}")
    print(f"    faixa valida       {p.min_value} .. {p.max_value}")
    print(f"\n  DESTINO")
    print(f"    protocolo          {p.target_protocol}")
    print(f"    endereco           {p.target_address}")
    print(f"    escala / offset    x{p.target_scale}  {p.target_offset:+}")
    print(f"\n  {CINZA}normalizacao: valor_bruto x {p.scale} "
          f"{'+ ' + str(p.offset) if p.offset else ''} -> {p.unit}{FIM}")
    if p.target_scale != 1.0 or p.target_offset:
        print(f"  {CINZA}codificacao no destino: "
              f"({p.unit} - {p.target_offset}) / {p.target_scale}{FIM}")
    return 0


def cmd_validar(args):
    caminho = Path(args.arquivo) if args.arquivo else CONFIG
    reg, erros = MappingRegistry.from_yaml(caminho)
    print(f"\nArquivo: {caminho.name}")
    print(f"{VERDE}Validos:    {len(reg)}{FIM}")
    print(f"{VERM if erros else VERDE}Rejeitados: {len(erros)}{FIM}")
    for e in erros:
        print(f"  {VERM}-> {e}{FIM}")
    return 1 if erros else 0


def cmd_cadastrar(args):
    dados = carregar_bruto(CONFIG)
    if any(p.get("point_id") == args.point_id for p in dados["points"]):
        print(f"{VERM}ponto '{args.point_id}' ja existe (use 'alterar'){FIM}")
        return 1

    novo = {
        "point_id": args.point_id,
        "source_protocol": args.source_protocol,
        "source_device": args.source_device or "",
        "source_address": str(args.source_address),
        "asset_id": args.asset_id,
        "asset_type": args.asset_type or "unknown",
        "measurement": args.measurement,
        "data_type": args.data_type,
        "unit": args.unit or "",
        "scale": args.scale,
        "offset": args.offset,
        "target_protocol": args.target_protocol or "",
        "target_address": str(args.target_address or ""),
        "target_scale": args.target_scale,
        "target_offset": args.target_offset,
    }
    if args.iec61850_hint:
        novo["iec61850_hint"] = args.iec61850_hint
    if args.min_value is not None:
        novo["min_value"] = args.min_value
    if args.max_value is not None:
        novo["max_value"] = args.max_value

    dados["points"].append(novo)

    erros = validar_antes_de_salvar(dados)
    if erros:
        print(f"{VERM}NAO GRAVADO - configuracao invalida:{FIM}")
        for e in erros:
            print(f"  {VERM}-> {e}{FIM}")
        return 1

    salvar_bruto(CONFIG, dados)
    print(f"{VERDE}ponto '{args.point_id}' cadastrado e validado{FIM}")
    return 0


def cmd_alterar(args):
    dados = carregar_bruto(CONFIG)
    alvo = next((p for p in dados["points"] if p.get("point_id") == args.point_id), None)
    if alvo is None:
        print(f"{VERM}ponto '{args.point_id}' nao encontrado{FIM}")
        return 1

    mapa = {
        "source_protocol": args.source_protocol, "source_device": args.source_device,
        "source_address": args.source_address, "asset_id": args.asset_id,
        "asset_type": args.asset_type, "measurement": args.measurement,
        "iec61850_hint": args.iec61850_hint, "data_type": args.data_type,
        "unit": args.unit, "scale": args.scale, "offset": args.offset,
        "min_value": args.min_value, "max_value": args.max_value,
        "target_protocol": args.target_protocol, "target_address": args.target_address,
        "target_scale": args.target_scale, "target_offset": args.target_offset,
    }
    alterados = []
    for campo, valor in mapa.items():
        if valor is not None:
            alvo[campo] = str(valor) if campo.endswith("address") else valor
            alterados.append(campo)

    if not alterados:
        print(f"{AMAR}nenhum campo informado{FIM}")
        return 1

    erros = validar_antes_de_salvar(dados)
    if erros:
        print(f"{VERM}NAO GRAVADO - a alteracao deixaria a configuracao invalida:{FIM}")
        for e in erros:
            print(f"  {VERM}-> {e}{FIM}")
        return 1

    salvar_bruto(CONFIG, dados)
    print(f"{VERDE}'{args.point_id}' atualizado: {', '.join(alterados)}{FIM}")
    return 0


def cmd_remover(args):
    dados = carregar_bruto(CONFIG)
    antes = len(dados["points"])
    dados["points"] = [p for p in dados["points"] if p.get("point_id") != args.point_id]
    if len(dados["points"]) == antes:
        print(f"{VERM}ponto '{args.point_id}' nao encontrado{FIM}")
        return 1
    salvar_bruto(CONFIG, dados)
    print(f"{VERDE}ponto '{args.point_id}' removido{FIM}")
    return 0


def cmd_capacidades(args):
    print("\nMatriz de capacidades dos protocolos de destino")
    print("(base da analise automatica de perda semantica)\n")
    print(loss_matrix({
        "OPC UA": OpcUaSink().capabilities,
        "Modbus": ModbusSink().capabilities,
        "DNP3 (nao implementado)": Dnp3Sink().capabilities,
    }))
    print(f"\n{CINZA}Um protocolo novo entra nesta matriz apenas declarando suas{FIM}")
    print(f"{CINZA}capacidades - a analise de perda passa a funcionar sem regra nova.{FIM}")
    return 0


def cmd_perdas(args):
    from core.store import Store
    store = Store(RAIZ / "gateway.db")
    linhas = store.loss_summary()
    if not linhas:
        print(f"{AMAR}nenhuma perda registrada (rode demo.py primeiro){FIM}")
        return 0
    print(f"\n{'TIPO':<14} {'CAMPO':<18} {'OCORRENCIAS':<12} MOTIVO")
    print("-" * 110)
    for r in linhas:
        motivo = (r["reason"] or "")[:60]
        print(f"{r['loss_type']:<14} {r['field']:<18} {r['total']:<12} {CINZA}{motivo}{FIM}")
    store.close()
    return 0


def cmd_fila(args):
    from core.store import Store
    store = Store(RAIZ / "gateway.db")
    c = store.counts()
    print("\nEstado da fila de eventos")
    print(f"  {VERDE}delivered   {c['delivered']}{FIM}")
    print(f"  {AMAR}pending     {c['pending']}{FIM}")
    print(f"  {AMAR}retry       {c['retry']}{FIM}")
    print(f"  {VERM}quarantine  {c['quarantine']}{FIM}")
    integro, quebra = store.verify_audit_chain()
    print(f"\n  trilha de auditoria: "
          f"{VERDE + 'integra' + FIM if integro else VERM + f'quebrada na seq {quebra}' + FIM}")
    store.close()
    return 0


# ---------------------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli.py", description="Gateway Multiprotocolo WEG - cadastro de pontos")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("listar", help="lista os pontos mapeados").set_defaults(func=cmd_listar)

    v = sub.add_parser("ver", help="detalha um ponto")
    v.add_argument("point_id")
    v.set_defaults(func=cmd_ver)

    val = sub.add_parser("validar", help="valida um arquivo de mapeamento")
    val.add_argument("--arquivo", help="caminho do YAML (padrao: config/points.yaml)")
    val.set_defaults(func=cmd_validar)

    sub.add_parser("capacidades", help="matriz de capacidades dos destinos").set_defaults(
        func=cmd_capacidades)
    sub.add_parser("perdas", help="perdas semanticas registradas").set_defaults(func=cmd_perdas)
    sub.add_parser("fila", help="estado da fila de eventos").set_defaults(func=cmd_fila)

    def campos_comuns(parser, obrigatorio: bool):
        req = obrigatorio
        parser.add_argument("--source-protocol", required=req)
        parser.add_argument("--source-device")
        parser.add_argument("--source-address", required=req)
        parser.add_argument("--asset-id", required=req)
        parser.add_argument("--asset-type")
        parser.add_argument("--measurement", required=req)
        parser.add_argument("--iec61850-hint")
        parser.add_argument("--data-type", default="float" if req else None,
                            choices=["bool", "int", "float", "string"])
        parser.add_argument("--unit")
        parser.add_argument("--scale", type=float, default=1.0 if req else None)
        parser.add_argument("--offset", type=float, default=0.0 if req else None)
        parser.add_argument("--min-value", type=float)
        parser.add_argument("--max-value", type=float)
        parser.add_argument("--target-protocol")
        parser.add_argument("--target-address")
        parser.add_argument("--target-scale", type=float, default=1.0 if req else None)
        parser.add_argument("--target-offset", type=float, default=0.0 if req else None)

    c = sub.add_parser("cadastrar", help="cadastra um novo ponto")
    c.add_argument("--point-id", required=True)
    campos_comuns(c, obrigatorio=True)
    c.set_defaults(func=cmd_cadastrar)

    a = sub.add_parser("alterar", help="altera campos de um ponto existente")
    a.add_argument("point_id")
    campos_comuns(a, obrigatorio=False)
    a.set_defaults(func=cmd_alterar)

    r = sub.add_parser("remover", help="remove um ponto")
    r.add_argument("point_id")
    r.set_defaults(func=cmd_remover)

    return p


if __name__ == "__main__":
    args = construir_parser().parse_args()
    sys.exit(args.func(args) or 0)
