/* ===========================================================================
   Dashboard do Gateway Multiprotocolo WEG

   Nao contem regra de negocio: so consome /api/* e apresenta.
   Atualizacao por polling simples (sem websocket - menos peca para quebrar
   durante uma apresentacao).
   =========================================================================== */

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

async function api(caminho, opcoes) {
    const r = await fetch(caminho, opcoes);
    return r.json();
}

async function post(caminho, corpo) {
    return api(caminho, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corpo || {}),
    });
}

const esc = (t) => String(t ?? "").replace(/[<>&"]/g,
    (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

/* ------------------------------------------------------------- navegacao */

$$("nav button").forEach((b) => {
    b.onclick = () => {
        $$("nav button").forEach((x) => x.classList.remove("ativo"));
        $$(".aba").forEach((x) => x.classList.remove("ativa"));
        b.classList.add("ativo");
        $("#aba-" + b.dataset.aba).classList.add("ativa");
        atualizar();
    };
});

/* ------------------------------------------------- formatacao de qualidade */

/* Convencao Kepware/Ignition: nunca apenas "Bad", sempre com submotivo. */
function classeEstado(estado) {
    return {
        delivered: "normal",
        pending: "degradado",
        retry: "falha",
        quarantine: "quarentena",
        comm_error: "falha",
        rejected: "falha",
        duplicate: "degradado",
    }[estado] || "normal";
}

function rotuloQualidade(q, origem) {
    if (!q) return "&mdash;";
    if (origem === "assumed") {
        return `<span class="estado degradado">${esc(q)} (sintetizada)</span>`;
    }
    if (q.startsWith("invalid")) return `<span class="estado falha">${esc(q)}</span>`;
    if (q.startsWith("questionable")) return `<span class="estado degradado">${esc(q)}</span>`;
    return `<span class="estado normal">${esc(q)}</span>`;
}

function hora(iso) {
    if (!iso) return "&mdash;";
    return `<span class="mono">${esc(String(iso).slice(11, 23))}</span>`;
}

/* -------------------------------------------------------------- atualizar */

let pontosCache = [];

async function atualizar() {
    const aba = document.querySelector("nav button.ativo").dataset.aba;

    const status = await api("/api/status");
    $("#ind-versao").textContent = status.mapping_version;
    $("#ind-pontos").textContent = status.pontos_validos;
    $("#ind-entregues").textContent = status.fila.delivered;

    const alerta = $("#ind-alerta");
    const problemas = [];
    if (status.destino_derrubado) problemas.push("destino fora do ar");
    if (status.fila.retry > 0) problemas.push(`${status.fila.retry} em retry`);
    if (status.fila.quarantine > 0) problemas.push(`${status.fila.quarantine} em quarentena`);
    alerta.innerHTML = problemas.length
        ? `<span class="estado falha">${esc(problemas.join(" &middot; "))}</span>`
        : "";

    if (aba === "visao") renderVisao(status);
    if (aba === "conexoes") renderConexoes(status);
    if (aba === "mapeamento") renderMapeamento();
    if (aba === "fila") renderFila(status);
    if (aba === "perdas") renderPerdas();
    if (aba === "capacidades") renderCapacidades();
    if (aba === "auditoria") renderAuditoria();
    if (aba === "traducao") carregarSelectPontos();
}

/* ------------------------------------------------------------ visao geral */

function renderVisao(status) {
    const f = status.fila;
    const origens = status.protocolos.filter((p) => p.papel === "origem");
    const conectadas = origens.filter((p) => p.conectado).length;

    const cards = [
        { rotulo: "Pontos mapeados", numero: status.pontos_validos,
          nota: `versao ${status.mapping_version}` },
        { rotulo: "Origens conectadas", numero: `${conectadas}/${origens.length}`,
          nota: "protocolos adquirindo",
          classe: conectadas < origens.length ? "anormal" : "" },
        { rotulo: "Entregues", numero: f.delivered, nota: "confirmados no destino" },
        { rotulo: "Aguardando", numero: f.pending + f.retry,
          nota: "pending + retry",
          classe: (f.pending + f.retry) > 0 ? "anormal" : "" },
        { rotulo: "Quarentena", numero: f.quarantine, nota: "rejeitados com motivo",
          classe: f.quarantine > 0 ? "quarentena" : "" },
        { rotulo: "Config rejeitada", numero: status.pontos_rejeitados,
          nota: "pontos invalidos isolados",
          classe: status.pontos_rejeitados > 0 ? "anormal" : "" },
    ];

    $("#cards-visao").innerHTML = cards.map((c) => `
        <div class="card ${c.classe || ""}">
            <div class="rotulo">${esc(c.rotulo)}</div>
            <div class="numero">${esc(c.numero)}</div>
            <div class="nota">${esc(c.nota)}</div>
        </div>`).join("");

    $("#tab-visao-conexoes").innerHTML = origens.map(linhaConexao).join("");
}

function linhaConexao(p) {
    const estado = p.conectado
        ? '<span class="estado normal">Good (comunicando)</span>'
        : '<span class="estado falha">Bad (Not Connected)</span>';
    return `<tr>
        <td class="mono">${esc(p.protocolo)}</td>
        <td>${esc(p.papel)}</td>
        <td class="mono">${esc(p.detalhe)}</td>
        <td>${estado}</td>
        <td class="submotivo">${esc(p.rotulo)}</td>
    </tr>`;
}

function renderConexoes(status) {
    $("#tab-conexoes").innerHTML = status.protocolos.map((p) => {
        const estado = p.conectado
            ? '<span class="estado normal">Good (comunicando)</span>'
            : '<span class="estado falha">Bad (Not Connected)</span>';
        return `<tr>
            <td class="mono">${esc(p.rotulo)}</td>
            <td>${esc(p.papel)}</td>
            <td class="mono">${esc(p.detalhe)}</td>
            <td>${estado}</td>
        </tr>`;
    }).join("");
}

/* ------------------------------------------------------------- mapeamento */

async function renderMapeamento() {
    const pontos = await api("/api/pontos");
    pontosCache = pontos;
    $("#tab-mapeamento").innerHTML = pontos.map((p) => `
        <tr>
            <td class="mono">${esc(p.point_id)}</td>
            <td>${esc(p.asset_id)}<div class="submotivo">${esc(p.asset_type)}</div></td>
            <td>${esc(p.measurement)}
                ${p.iec61850_hint ? `<div class="submotivo mono">${esc(p.iec61850_hint)}</div>` : ""}</td>
            <td class="mono">${esc(p.source_protocol)}</td>
            <td class="mono">${esc(p.source_address)}</td>
            <td class="mono">${esc(p.data_type)}</td>
            <td class="mono">${esc(p.unit)}</td>
            <td class="num">&times;${esc(p.scale)}</td>
            <td class="mono">${esc(p.target_protocol)}</td>
            <td class="mono">${esc(p.target_address)}</td>
        </tr>`).join("");
}

/* --------------------------------------------------------------- traducao */

async function carregarSelectPontos() {
    if (pontosCache.length === 0) pontosCache = await api("/api/pontos");
    const sel = $("#sel-ponto");
    if (sel.options.length > 0) return;
    sel.innerHTML = pontosCache.map((p) =>
        `<option value="${esc(p.point_id)}">${esc(p.source_protocol)} &rarr; ${esc(p.target_protocol)}
         | ${esc(p.point_id)}</option>`).join("");
}

$("#btn-traduzir").onclick = async () => {
    const pid = $("#sel-ponto").value;
    $("#traducao-msg").textContent = "executando...";
    const r = await post("/api/traduzir", { point_id: pid });
    $("#traducao-msg").textContent = "";
    renderResultadoTraducao(r);
};

function renderResultadoTraducao(r) {
    const alvo = $("#resultado-traducao");

    if (r.erro) {
        alvo.innerHTML = `<div class="aviso">${esc(r.erro)}</div>`;
        return;
    }

    const cls = classeEstado(r.state);

    /* falha de leitura: nao ha amostra canonica */
    if (!r.canonico) {
        alvo.innerHTML = `
            <div class="aviso" style="border-left-color:var(--falha)">
                <b class="estado falha">${esc(r.state)}</b> &mdash; ${esc(r.detail)}
            </div>
            <div class="lineage">
                <div class="no">
                    <div class="no-titulo">Origem &mdash; ${esc(r.origem.protocolo)}</div>
                    <dl>
                        <dt>dispositivo</dt><dd>${esc(r.origem.dispositivo)}</dd>
                        <dt>endereco</dt><dd>${esc(r.origem.endereco)}</dd>
                        <dt>leitura</dt><dd class="estado falha">sem resposta</dd>
                    </dl>
                </div>
                <div class="seta com-perda">
                    <span>&#10142;</span>
                    <span class="rotulo-perda">leitura<br>interrompida</span>
                </div>
                <div class="no"><div class="no-titulo">Modelo Canonico</div>
                    <div class="vazio">nenhuma amostra gerada</div></div>
            </div>`;
        return;
    }

    const c = r.canonico;
    const perdasPorCampo = {};
    r.perdas.forEach((p) => { perdasPorCampo[p.field] = p; });

    const pilar = (nome, valor, campo, sintetizado) => {
        let classe = "";
        if (perdasPorCampo[campo]) {
            classe = perdasPorCampo[campo].loss_type === "dropped" ? "perdido" : "sintetizado";
        } else if (sintetizado) {
            classe = "sintetizado";
        }
        return `<span class="pilar ${classe}">${esc(nome)} <b>${esc(valor)}</b></span>`;
    };

    const temPerda = r.perdas.length > 0;

    alvo.innerHTML = `
        <div class="lineage">
            <div class="no">
                <div class="no-titulo">1. Origem &mdash; ${esc(r.origem.protocolo)}</div>
                <dl>
                    <dt>dispositivo</dt><dd>${esc(r.origem.dispositivo)}</dd>
                    <dt>endereco</dt><dd>${esc(r.origem.endereco)}</dd>
                    <dt>valor bruto</dt><dd>${esc(r.origem.valor_bruto)}</dd>
                </dl>
            </div>

            <div class="seta">&#10142;</div>

            <div class="no canonico">
                <div class="no-titulo">2. Modelo Canonico &mdash; os 5 pilares</div>
                <dl>
                    <dt>valor</dt><dd>${esc(c.valor)} ${esc(c.unidade)}</dd>
                    <dt>normalizacao</dt><dd>&times;${esc(c.escala)} ${c.offset ? "+" + esc(c.offset) : ""}</dd>
                    <dt>event_id</dt><dd class="submotivo">${esc(c.event_id.slice(0, 8))}</dd>
                </dl>
                <div class="pilares">
                    ${pilar("significado", c.significado.iec61850_hint || c.significado.measurement, "semantic")}
                    ${pilar("tipo", c.tipo, "data_type")}
                    ${pilar("unidade", c.unidade || "-", "unit")}
                    ${pilar("qualidade", c.qualidade.valor, "quality", !c.qualidade.nativa)}
                    ${pilar("timestamp", c.timestamp.valor.slice(11, 19), "timestamp", c.timestamp.aproximado)}
                </div>
            </div>

            <div class="seta ${temPerda ? "com-perda" : ""}">
                <span>&#10142;</span>
                ${temPerda
                    ? `<span class="rotulo-perda">${r.perdas.length} metadados<br>nao representaveis</span>`
                    : ""}
            </div>

            <div class="no">
                <div class="no-titulo">3. Destino &mdash; ${esc(r.destino.protocolo)}</div>
                <dl>
                    <dt>endereco</dt><dd>${esc(r.destino.endereco)}</dd>
                    <dt>escala dest.</dt><dd>&times;${esc(r.destino.escala)}</dd>
                    <dt>estado</dt><dd class="estado ${cls}">${esc(r.state)}</dd>
                </dl>
            </div>
        </div>

        ${temPerda ? `
        <h2 style="margin-top:20px">Perdas registradas nesta conversao</h2>
        <table>
            <thead><tr><th>Tipo</th><th>Campo</th><th>Origem</th><th>Destino</th><th>Motivo</th></tr></thead>
            <tbody>${r.perdas.map((p) => `
                <tr class="tem-perda">
                    <td class="mono ${p.loss_type === "dropped" ? "estado falha" : "estado degradado"}">${esc(p.loss_type)}</td>
                    <td class="mono">${esc(p.field)}</td>
                    <td class="mono">${esc(p.source_value)}</td>
                    <td class="mono">${esc(p.target_value)}</td>
                    <td class="submotivo">${esc(p.reason)}</td>
                </tr>`).join("")}</tbody>
        </table>` : `
        <div class="aviso" style="border-left-color:var(--ok-discreto)">
            Nenhuma perda semantica: o protocolo de destino representa todos os metadados
            presentes na origem.
        </div>`}`;
}

/* ------------------------------------------------------------------- fila */

async function renderFila(status) {
    const f = status.fila;
    const total = Math.max(1, f.delivered + f.pending + f.retry + f.quarantine);
    const seg = (chave, valor) => valor === 0 ? "" :
        `<div class="seg-${chave}" style="width:${(valor / total) * 100}%">${valor}</div>`;

    $("#barra-fila").innerHTML =
        seg("delivered", f.delivered) + seg("pending", f.pending) +
        seg("retry", f.retry) + seg("quarantine", f.quarantine);

    const eventos = await api("/api/eventos?limite=40");
    $("#tab-eventos").innerHTML = eventos.length === 0
        ? '<tr><td colspan="8" class="vazio">nenhum evento processado ainda</td></tr>'
        : eventos.map((e) => {
            const cls = classeEstado(e.state);
            const suspeito = e.state === "quarantine";
            return `<tr>
                <td><span class="estado ${cls}">${esc(e.state)}</span>
                    ${e.last_error ? `<div class="submotivo">${esc(e.last_error)}</div>` : ""}</td>
                <td class="mono">${esc(e.point_id)}</td>
                <td>${esc(e.asset_id)}<div class="submotivo">${esc(e.measurement)}</div></td>
                <td class="mono ${suspeito ? "valor-suspeito" : ""}">${esc(e.value)} ${esc(e.unit)}
                    <div class="submotivo">bruto ${esc(e.raw_value)}</div></td>
                <td>${rotuloQualidade(e.quality, e.quality_origin)}</td>
                <td class="mono">${esc(e.source_protocol)}</td>
                <td class="num">${esc(e.attempts)}</td>
                <td>${hora(e.created_at)}</td>
            </tr>`;
        }).join("");
}

$("#btn-flush").onclick = async () => {
    $("#flush-msg").textContent = "reenviando...";
    const r = await post("/api/flush");
    $("#flush-msg").textContent = `${r.delivered} entregues, ${r.failed} falharam`;
    atualizar();
};

/* ----------------------------------------------------------------- perdas */

async function renderPerdas() {
    const d = await api("/api/perdas");

    $("#tab-perdas-resumo").innerHTML = d.resumo.length === 0
        ? '<tr><td colspan="4" class="vazio">nenhuma perda registrada</td></tr>'
        : d.resumo.map((r) => `
            <tr class="tem-perda">
                <td class="mono ${r.loss_type === "dropped" ? "estado falha" : "estado degradado"}">${esc(r.loss_type)}</td>
                <td class="mono">${esc(r.field)}</td>
                <td class="num">${esc(r.total)}</td>
                <td class="submotivo">${esc((r.reason || "").slice(0, 90))}</td>
            </tr>`).join("");

    $("#tab-perdas-recentes").innerHTML = d.recentes.length === 0
        ? '<tr><td colspan="6" class="vazio">nenhum registro</td></tr>'
        : d.recentes.map((r) => `
            <tr class="tem-perda">
                <td class="mono ${r.loss_type === "dropped" ? "estado falha" : "estado degradado"}">${esc(r.loss_type)}</td>
                <td class="mono">${esc(r.field)}</td>
                <td class="mono">${esc(r.source_value)}</td>
                <td class="mono">${esc(r.target_value)}</td>
                <td class="submotivo">${esc(r.reason)}</td>
                <td class="mono submotivo">${esc(r.mapping_version)}</td>
            </tr>`).join("");
}

/* ------------------------------------------------------------ capacidades */

async function renderCapacidades() {
    const caps = await api("/api/capacidades");
    const marca = (v) => v
        ? '<span class="estado normal">sim</span>'
        : '<span class="estado falha">nao</span>';

    $("#tab-capacidades").innerHTML = caps.map((c) => `
        <tr>
            <td class="mono">${esc(c.protocolo)}</td>
            <td>${c.implementado
                ? '<span class="estado normal">sim</span>'
                : '<span class="estado degradado">declarado</span>'}</td>
            <td>${marca(c.qualidade)}</td>
            <td>${marca(c.timestamp)}</td>
            <td>${marca(c.unidade)}</td>
            <td>${marca(c.semantica)}</td>
            <td>${marca(c.float)}</td>
            <td class="num">${esc(c.bits)}</td>
            <td class="num">${esc(c.estados_qualidade)}</td>
        </tr>`).join("");
}

/* ------------------------------------------------------------- auditoria */

async function renderAuditoria() {
    const d = await api("/api/auditoria");

    $("#cadeia-estado").innerHTML = d.integra
        ? '<span class="cadeia-ok">INTEGRA</span>'
        : '<span class="cadeia-falha">QUEBRADA</span>';
    $("#card-cadeia").className = "card" + (d.integra ? "" : " critico");
    $("#cadeia-nota").textContent = d.integra
        ? `${d.entradas.length} entradas verificadas`
        : `quebra detectada na sequencia ${d.quebra_em}`;

    $("#tab-auditoria").innerHTML = d.entradas.length === 0
        ? '<tr><td colspan="5" class="vazio">sem registros</td></tr>'
        : d.entradas.map((e) => `
            <tr>
                <td class="num">${esc(e.seq)}</td>
                <td class="mono">${esc(e.action)}</td>
                <td class="submotivo">${esc(e.detail)}</td>
                <td class="mono submotivo">${esc(e.entry_hash.slice(0, 12))}</td>
                <td>${hora(e.created_at)}</td>
            </tr>`).join("");
}

/* -------------------------------------------------------------- ensaios */

$("#btn-derrubar").onclick = async () => {
    const r = await post("/api/falha", { tipo: "destino", ligar: true });
    $("#destino-msg").textContent = r.mensagem;
    atualizar();
};

$("#btn-religar").onclick = async () => {
    const r = await post("/api/falha", { tipo: "destino", ligar: false });
    $("#destino-msg").textContent = r.mensagem;
    atualizar();
};

$$("[data-falha]").forEach((b) => {
    b.onclick = async () => {
        const r = await post("/api/falha", {
            tipo: b.dataset.falha,
            protocolo: b.dataset.proto,
            ligar: b.dataset.ligar === "1",
        });
        $("#" + b.dataset.falha + "-msg").textContent = r.mensagem;
    };
});

/* --------------------------------------------------------------- inicio */

atualizar();
setInterval(atualizar, 2500);
