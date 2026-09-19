# Guia de Domínio — Gateway Multiprotocolo WEG

Este guia existe pra você conseguir explicar o projeto **sem mim do lado**. Não é documentação técnica de referência (isso já existe no `README.md`) — é um guia de estudo, pensado pra você entender o *porquê* de cada peça e conseguir defender com confiança.

---

## PARTE 1 — O problema, em uma frase

> A WEG monitora transformadores. Cada sensor fala um protocolo diferente (Modbus, IEC 61850, DNP3, OPC UA). Traduzir entre eles não é trocar formato de pacote — é reconstruir **significado**, e cada protocolo carrega uma quantidade diferente de significado embutido.

Se você só entender uma coisa deste guia, entenda isto: **Modbus é burro** (só manda número), **IEC 61850 é esperto** (já vem com contexto, qualidade, hora). Traduzir de esperto pra burro **perde informação, sempre, estruturalmente**. Não tem jeito de evitar isso — é física do protocolo, não falta de esforço nosso.

---

## PARTE 2 — Por que "nunca perde" é a frase ERRADA, e qual é a certa

### O erro que você não pode cometer no pitch

Se você disser "nosso sistema não perde dado", um engenheiro da WEG vai te desmontar em 10 segundos: *"como assim não perde? Modbus não tem campo de qualidade, é impossível não perder."* E ele vai ter razão.

### A frase certa, que você precisa decorar

> **"Nosso sistema nunca perde dado EM SILÊNCIO. Toda perda é detectada, registrada com motivo, e rastreável até o evento de origem."**

A diferença entre essas duas frases é o projeto inteiro. Perda vai acontecer sempre que o destino for mais pobre que a origem — isso é inevitável. O que NÃO é inevitável, e é onde mora o nosso diferencial, é:
- Hoje (sem o nosso sistema), essa perda acontece **e ninguém sabe**. O valor chega, bonito, no destino, e a informação que sumiu (qualidade, hora, significado) simplesmente não existe mais em lugar nenhum.
- Com o nosso sistema, a perda **é um registro no banco**, com campo, motivo, valor de origem, valor de destino, e a versão da regra que causou. Você pode consultar depois: "quanto perdemos, de quê, e por quê".

### A prova viva disso (decore este contraste, é o momento mais forte do pitch)

Rode os dois pontos abaixo na aba **Tradução ao Vivo** do dashboard, um atrás do outro:

1. `iec61850-mms → modbus | TR02_OIL_TEMP_MMS` — mesma leitura de sensor rico, indo pra um destino pobre. Resultado: **5 perdas registradas**, pilares riscados em vermelho.
2. `dnp3 → opcua | BAT01_VOLTAGE_DNP3` — leitura de outro sensor, indo pra um destino rico. Resultado: **zero perdas**, tela toda neutra.

**É a mesma lógica de software rodando nos dois casos.** A diferença de resultado não é bug nem sorte — é porque o destino escolhido é diferente. Isso prova, sem você precisar dizer uma palavra, que o sistema está *medindo* a realidade, não inventando números bonitos.

### O verdadeiro diferencial técnico (a parte que poucos hackathons vão ter)

A maioria dos concorrentes, se resolverem fazer algo parecido, vai escrever regras do tipo:
```
se origem = MMS e destino = Modbus: perde qualidade, timestamp, unidade...
se origem = DNP3 e destino = Modbus: perde qualidade, unidade...
```
Isso é **uma regra por par de protocolos**. Com 4 protocolos, são até 12 pares — e cada protocolo novo que entra exige reescrever regras pros pares antigos também.

**O que fizemos diferente**: cada protocolo de destino **declara o que consegue representar** (um objeto `SinkCapabilities`: suporta qualidade? sim/não. Suporta timestamp? sim/não...). O motor de perda (`core/loss.py`) **compara** a amostra com essa declaração e **deriva** a perda automaticamente. Não existe regra por par. Por isso o DNP3, que nem tem a pilha de protocolo implementada de verdade, **já aparece corretamente analisado** na matriz de capacidades — só porque declarou suas capacidades.

Isso significa: **adicionar um protocolo novo no futuro custa 1 arquivo novo, não N regras novas.** Essa é a frase que prova que você entende arquitetura de software, não só que "fez funcionar".

---

## PARTE 3 — Como construímos (a arquitetura, peça por peça)

Pensa nisso como uma linha de montagem com 6 estações. Cada uma tem UM trabalho, e só um.

```
Sensor → [ADAPTER DE ORIGEM] → [MODELO CANÔNICO] → [REGISTRY] → [STORE] → [MOTOR DE PERDA] → [ADAPTER DE DESTINO] → Sistema do cliente
```

### Estação 1 — `adapters/` (os "tradutores de idioma")
Cada arquivo aqui fala **um** protocolo (Modbus, MMS, DNP3, OPC UA) e só ele. É o único lugar do projeto que importa `pymodbus`, `pyiec61850`, `asyncua`. A regra de ouro: **se eu trocar a biblioteca de um protocolo amanhã, só esse arquivo muda — nada mais no projeto sente a mudança.**

*Pra que serve:* isolar o "sotaque" de cada protocolo, pra que o resto do sistema nunca precise saber a diferença entre um `MmsValue` e um `ModbusRegister`.

### Estação 2 — `core/canonical.py` (o "idioma neutro")
Define o formato único que TODOS os dados assumem depois de traduzidos: `CanonicalSample`. Tem os **5 pilares** que o desafio da WEG pede: significado, tipo, unidade, qualidade, timestamp. Cada pilar tem um campo `origin` que diz se aquele dado veio **de verdade do sensor** (`native`) ou se **o gateway inventou** porque a origem não tinha (`assumed`/`synthesized`).

*Pra que serve:* ser o "esperanto" entre os protocolos — todo mundo traduz PRA ele e DELE, nunca direto de um protocolo pro outro.

### Estação 3 — `core/ports.py` (a fronteira)
Define os "contratos" (`SourceAdapter`, `SinkAdapter`, `SinkCapabilities`) que separam os adapters do núcleo. É aqui que mora o requisito de "não depender de biblioteca de protocolo" — o núcleo só conhece essas interfaces abstratas.

*Pra que serve:* ser a parede entre "mundo que conhece Modbus" e "mundo que só conhece o canônico".

### Estação 4 — `core/registry.py` (o "de-para")
Guarda a configuração: qual ponto vem de onde, com que escala, pra onde vai. É o `config/points.yaml`. Também valida tudo na entrada — se você cadastrar um ponto com escala zero ou faixa invertida, ele **recusa e explica o motivo**, não deixa entrar quebrado.

*Pra que serve:* ser o "livro de regras" que qualquer pessoa (não só programador) pode editar sem recompilar nada — é o requisito de parametrização do desafio.

### Estação 5 — `core/store.py` (a memória que nunca esquece)
Banco SQLite com 3 tabelas: `events` (fila com estados pending/delivered/retry/quarantine), `losses` (cada perda registrada), `audit` (trilha encadeada por hash). Todo evento é salvo **antes** de tentar publicar — se o sistema cair no meio, nada se perde.

*Pra que serve:* garantir que "nunca perde em silêncio" não seja só discurso — é o banco de dados garantindo isso na prática.

### Estação 6 — `core/loss.py` (o motor de perda)
Já explicado na Parte 2 — compara a amostra canônica com as capacidades declaradas do destino e gera os registros de perda automaticamente.

### Estação 7 — `core/engine.py` (o maestro)
Orquestra tudo: chama o adapter de origem, valida, monta o canônico, salva na fila, analisa perda, chama o adapter de destino, atualiza o estado final. É o único arquivo que "sabe a sequência toda" — e mesmo ele não conhece bibliotecas de protocolo, só as interfaces.

---

## PARTE 4 — Como usar, na prática

### Rodar a demonstração completa (prova em texto, cenário por cenário)
```bash
cd /mnt/c/Users/gusta/Documents/Hackathon-WEG-Integre-2026/gateway
source /root/venv-weg/bin/activate   # (ou use os caminhos completos, como fizemos)
bash run_demo.sh
```

### Usar a CLI (cadastro de pontos, requisito de parametrização)
```bash
python cli.py listar                  # ver o de-para
python cli.py ver <point_id>          # detalhar um ponto
python cli.py cadastrar --point-id ... --scale 0.1 ...
python cli.py alterar <point_id> --scale 0.5
python cli.py capacidades             # matriz de capacidades
python cli.py perdas                  # perdas acumuladas
```

### Subir o dashboard (a apresentação visual)
```bash
bash web/run.sh
```
Abre em `http://localhost:8080`. Navegue: **Visão Geral** (prova que está rodando de verdade) → **Tradução ao Vivo** (o coração do pitch, o contraste da Parte 2) → **Ensaios de Falha** (derruba destino, mostra fila acumulando e sendo reenviada) → **Auditoria** (a trilha encadeada).

---

## PARTE 5 — Perguntas que vão te fazer, e a resposta pronta

**"Por que vocês implementaram só 2 protocolos de verdade?"**
> "Porque o próprio desafio permite, desde que a arquitetura preveja os outros — e a nossa prova disso é que o DNP3, mesmo sem pilha de protocolo implementada, já aparece corretamente na análise de perda, só por ter declarado suas capacidades."

**"Por que DNP3 é simulado e não real?"**
> "Verificamos: a biblioteca de referência OpenDNP3 está arquivada no GitHub desde 2022, e o binding Python não recebe atualização desde 2018. Não íamos colocar o caminho crítico do projeto em cima de uma dependência morta. O PDF do desafio permite dados simulados — usamos, respeitando a semântica real do protocolo (Group/Variation/Index, flags de qualidade), não inventando valores aleatórios."

**"Isso substitui um conversor comercial de verdade?"**
> "Não no sentido de certificação, robustez elétrica e redundância — isso está fora do escopo de um protótipo de hackathon. O que provamos é que a lógica de tradução semântica pode rodar em software, sobre infraestrutura que a WEG já tem, reduzindo a dependência dos conversores comerciais para os casos cobertos."

**"O que acontece se o sensor cair no meio de uma leitura?"**
> [mostra ao vivo no dashboard, aba Ensaios de Falha] "O evento fica em retry, nada é perdido, e quando o sensor volta, tudo é reenviado automaticamente."

**"Como vocês garantem que a perda registrada é real, não decorativa?"**
> "O motor de perda não tem regra por par de protocolo — ele compara a amostra com a capacidade declarada do destino. Isso significa que a perda é *derivada*, não *hard-coded*. Dá pra provar isso mostrando que o DNP3, mesmo não implementado, já participa corretamente da análise."

---

## PARTE 6 — Vocabulário que você precisa dominar de cor

| Termo | O que significa, em uma frase |
|---|---|
| **Modelo canônico** | O formato único e neutro que todo dado assume no meio do caminho |
| **Os 5 pilares** | Significado, tipo, unidade, qualidade, timestamp — o que precisa ser preservado |
| **De-para** | A tabela de configuração que diz origem→destino de cada ponto |
| **Adapter** | O tradutor de um protocolo específico; único lugar que conhece a biblioteca daquele protocolo |
| **Capacidades declaradas** | O que cada protocolo de destino admite representar — usado pra derivar a perda automaticamente |
| **Perda sintetizada (synthesized)** | Metadado que não existia na origem e foi inventado pelo gateway (ex: timestamp de recebimento) |
| **Perda descartada (dropped)** | Metadado que existia na origem e não coube no destino |
| **Quarentena** | Estado de um evento com valor implausível — não é descartado, fica registrado com motivo |
| **Retry** | Estado de um evento que falhou ao publicar e será reenviado |
| **Trilha de auditoria encadeada** | Cada registro de log guarda o hash do anterior — se alguém altera um registro antigo, a cadeia quebra e isso é detectável |

---

**Se você entender e conseguir explicar cada linha deste guia sem olhar, você está pronto pra defender o projeto pra qualquer engenheiro da WEG.**
