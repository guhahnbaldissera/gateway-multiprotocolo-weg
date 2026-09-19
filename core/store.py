"""
Persistencia: fila de eventos, registro de perdas e trilha de auditoria.

Principio: NENHUM DADO DESAPARECE EM SILENCIO.
Todo evento esta sempre em um estado verificavel:

    pending     -> recebido e persistido, ainda nao publicado
    delivered   -> confirmado no destino
    retry       -> falha de publicacao, sera reenviado
    quarantine  -> rejeitado (configuracao invalida ou valor fora de faixa)

O evento e gravado ANTES da tentativa de publicacao. Se o gateway reiniciar no
meio do envio, o evento continua la. Isso espelha o comportamento que o proprio
TFM ja tem no dispositivo edge (armazenamento offline), estendido para a camada
de traducao semantica.

Garantia prometida: ENTREGA PELO MENOS UMA VEZ, com deteccao de duplicidade.
Nao prometemos exactly-once - e caro e desnecessario aqui.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .canonical import CanonicalSample
from .loss import LossRecord


class EventState(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    RETRY = "retry"
    QUARANTINE = "quarantine"


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    sequence        INTEGER,
    point_id        TEXT,
    payload         TEXT NOT NULL,
    state           TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    payload_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_state ON events(state);

CREATE TABLE IF NOT EXISTS losses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    field           TEXT NOT NULL,
    loss_type       TEXT NOT NULL,
    source_value    TEXT,
    target_value    TEXT,
    reason          TEXT,
    mapping_version TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_losses_event ON losses(event_id);

CREATE TABLE IF NOT EXISTS audit (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    detail          TEXT,
    prev_hash       TEXT,
    entry_hash      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path = "gateway.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    # eventos
    # ------------------------------------------------------------------

    @staticmethod
    def payload_hash(sample: CanonicalSample) -> str:
        base = json.dumps(sample.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(base.encode()).hexdigest()

    def enqueue(self, sample: CanonicalSample, point_id: str,
                state: EventState = EventState.PENDING,
                error: str | None = None) -> bool:
        """
        Persiste o evento ANTES de qualquer tentativa de publicacao.
        Retorna False se o event_id ja existia (duplicidade detectada).
        """
        try:
            self.conn.execute(
                "INSERT INTO events (event_id, sequence, point_id, payload, state, "
                "attempts, last_error, payload_hash, created_at, updated_at) "
                "VALUES (?,?,?,?,?,0,?,?,?,?)",
                (sample.event_id, sample.sequence, point_id,
                 json.dumps(sample.to_dict(), default=str), state.value,
                 error, self.payload_hash(sample), _now(), _now()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.audit("duplicate_rejected", f"event_id {sample.event_id} ja existia")
            return False

    def mark(self, event_id: str, state: EventState, error: str | None = None,
             bump_attempts: bool = False) -> None:
        if bump_attempts:
            self.conn.execute(
                "UPDATE events SET state=?, last_error=?, attempts=attempts+1, updated_at=? "
                "WHERE event_id=?", (state.value, error, _now(), event_id))
        else:
            self.conn.execute(
                "UPDATE events SET state=?, last_error=?, updated_at=? WHERE event_id=?",
                (state.value, error, _now(), event_id))
        self.conn.commit()

    def pending_batch(self, limit: int = 100) -> list[sqlite3.Row]:
        """Eventos aguardando entrega (novos + reenvios), em ordem de chegada."""
        return list(self.conn.execute(
            "SELECT * FROM events WHERE state IN (?,?) ORDER BY sequence LIMIT ?",
            (EventState.PENDING.value, EventState.RETRY.value, limit)))

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) c FROM events GROUP BY state")
        base = {s.value: 0 for s in EventState}
        base.update({r["state"]: r["c"] for r in rows})
        return base

    def exists(self, event_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone() is not None

    # ------------------------------------------------------------------
    # perdas semanticas
    # ------------------------------------------------------------------

    def record_losses(self, losses: list[LossRecord]) -> None:
        for l in losses:
            self.conn.execute(
                "INSERT INTO losses (event_id, field, loss_type, source_value, "
                "target_value, reason, mapping_version, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (l.event_id, l.field, l.loss_type.value, l.source_value,
                 l.target_value, l.reason, l.mapping_version, _now()))
        self.conn.commit()

    def losses_for(self, event_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM losses WHERE event_id=?", (event_id,)))

    def loss_summary(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT field, loss_type, COUNT(*) total, reason FROM losses "
            "GROUP BY field, loss_type ORDER BY total DESC"))

    # ------------------------------------------------------------------
    # auditoria encadeada (detecta adulteracao - NAO e blockchain)
    # ------------------------------------------------------------------

    def audit(self, action: str, detail: str = "") -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["entry_hash"] if row else ""
        agora = _now()
        entry = hashlib.sha256(f"{prev}{action}{detail}{agora}".encode()).hexdigest()
        self.conn.execute(
            "INSERT INTO audit (action, detail, prev_hash, entry_hash, created_at) "
            "VALUES (?,?,?,?,?)", (action, detail, prev, entry, agora))
        self.conn.commit()
        return entry

    def verify_audit_chain(self) -> tuple[bool, int | None]:
        """Retorna (integra, seq_da_primeira_quebra)."""
        prev = ""
        for row in self.conn.execute("SELECT * FROM audit ORDER BY seq"):
            esperado = hashlib.sha256(
                f"{prev}{row['action']}{row['detail']}{row['created_at']}".encode()).hexdigest()
            if esperado != row["entry_hash"] or row["prev_hash"] != prev:
                return False, row["seq"]
            prev = row["entry_hash"]
        return True, None

    def close(self) -> None:
        self.conn.close()
