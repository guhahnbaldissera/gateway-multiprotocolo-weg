"""
Adaptador de DESTINO - DNP3  [NAO IMPLEMENTADO FUNCIONALMENTE]

Este arquivo existe para atender o requisito de EXTENSIBILIDADE do desafio:
evidenciar na arquitetura como um protocolo ainda nao implementado seria
incorporado, sem alterar o nucleo.

O que ja esta pronto aqui:
    - contrato cumprido (herda SinkAdapter)
    - capacidades DECLARADAS -> o motor de perda semantica ja sabe analisar
      conversoes para DNP3 sem precisar de nenhuma regra nova

O que falta:
    - a pilha de protocolo em si (encode das Object Group/Variation e a sessao)

Por que nao foi implementado: a stack de referencia (OpenDNP3) esta ARQUIVADA
no GitHub desde 2022-05-18, e o binding Python (pydnp3) teve sua ultima versao
publicada no PyPI em 2018-06-01. Verificado em 2026-09-19. Colocar o caminho
critico do projeto sobre uma dependencia morta seria imprudente - entao o DNP3
entra como adaptador previsto, com capacidades declaradas e caminho de
implementacao descrito.

Como seria implementado (sem tocar no nucleo):
    1. escolher a pilha (stack comercial, ou porte proprio do encode DNP3)
    2. mapear o tipo canonico -> Object Group / Variation:
           float com timestamp  -> Group 32 Var 7  (Analog Input Event)
           float estatico       -> Group 30 Var 5  (Analog Input)
           booleano             -> Group 1  Var 2  (Binary Input with flags)
    3. mapear Quality canonica -> byte de flags (online/restart/comm-lost/...)
    4. target_address no de-para passa a ser "grupo.variacao.indice" (ex: 30.5.14)
    5. registrar a classe do evento (Class 1/2/3) para o buffer de eventos
Nenhum desses passos altera engine.py, canonical.py, loss.py ou registry.py.
"""

from __future__ import annotations

from core.canonical import CanonicalSample
from core.ports import (PublishResult, SinkAdapter, SinkCapabilities,
                        TargetBinding)


class Dnp3Sink(SinkAdapter):
    protocol_name = "dnp3"
    adapter_version = "0.0-nao-implementado"
    implemented = False

    @property
    def capabilities(self) -> SinkCapabilities:
        """
        Capacidades reais do DNP3, declaradas mesmo sem a pilha implementada.
        Isso basta para o motor calcular a perda semantica de qualquer origem
        para DNP3 - demonstrando que a analise de perda e derivada do modelo,
        nao escrita a mao por par de protocolos.
        """
        return SinkCapabilities(
            supports_quality=True,          # 1 byte de flags
            supports_timestamp=True,        # 48 bits, se a Variation incluir
            supports_unit=False,            # DNP3 nao carrega unidade de engenharia
            supports_float=True,            # Group 32 Var 5/7
            supports_semantic_model=False,  # so grupo/variacao/indice, sem significado
            numeric_bits=32,
            quality_states=8,               # bem mais pobre que a bit-string do 61850
        )

    def connect(self) -> None:
        raise NotImplementedError(
            "adaptador DNP3 previsto na arquitetura, ainda sem pilha de protocolo")

    def publish(self, sample: CanonicalSample, binding: TargetBinding) -> PublishResult:
        return PublishResult(
            ok=False,
            detail="DNP3 declarado na arquitetura mas nao implementado "
                   "(capacidades ja participam da analise de perda)")

    def close(self) -> None:
        pass
