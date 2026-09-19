"""
Registry de pontos - o "de-para" configuravel.

REQUISITO WEG: interface para "cadastrar um novo ponto, alterar o endereco de
origem ou de destino mapeados, definir os protocolos de origem e destino,
selecionar os tipos de dados envolvidos, configurar fatores de escala e offset
e definir unidades de engenharia".

Arquitetura em duas etapas (evita explosao N x N de conversores):

    origem --[de-para 1]--> CANONICO --[de-para 2]--> destino

Adicionar um protocolo novo custa 1 adaptador + 1 declaracao de capacidades,
nao N conversores novos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .canonical import DataType


class InvalidMappingError(Exception):
    """Configuracao invalida detectada no carregamento (nao em runtime)."""

    def __init__(self, point_id: str, problem: str):
        self.point_id = point_id
        self.problem = problem
        super().__init__(f"ponto '{point_id}': {problem}")


@dataclass
class PointMapping:
    point_id: str

    # --- origem ---
    source_protocol: str
    source_device: str
    source_address: str

    # --- significado (PILAR 1) ---
    asset_id: str
    asset_type: str
    measurement: str
    iec61850_hint: str | None = None

    # --- normalizacao ---
    data_type: DataType = DataType.FLOAT
    unit: str = ""
    scale: float = 1.0
    offset: float = 0.0

    # --- validacao de entrada ---
    min_value: float | None = None
    max_value: float | None = None

    # --- destino (escala propria, independente da escala de origem) ---
    target_protocol: str = ""
    target_address: str = ""
    target_scale: float = 1.0
    target_offset: float = 0.0

    enabled: bool = True

    def binding(self):
        from .ports import TargetBinding
        return TargetBinding(address=self.target_address,
                             scale=self.target_scale,
                             offset=self.target_offset)

    def normalize(self, raw_value) -> float | int | bool | str:
        """Aplica escala e offset. Modbus 253 * 0.1 = 25.3 C"""
        if self.data_type in (DataType.FLOAT, DataType.INT):
            valor = (float(raw_value) * self.scale) + self.offset
            return valor if self.data_type is DataType.FLOAT else int(round(valor))
        if self.data_type is DataType.BOOL:
            return bool(raw_value)
        return str(raw_value)

    def validate_value(self, value) -> str | None:
        """Retorna motivo da rejeicao, ou None se valido."""
        if self.data_type in (DataType.FLOAT, DataType.INT):
            if self.min_value is not None and value < self.min_value:
                return f"valor {value} abaixo do minimo configurado ({self.min_value} {self.unit})"
            if self.max_value is not None and value > self.max_value:
                return f"valor {value} acima do maximo configurado ({self.max_value} {self.unit})"
        return None


class MappingRegistry:
    def __init__(self, version: str = "1"):
        self.version = version
        self._points: dict[str, PointMapping] = {}

    # ------------------------------------------------------------------
    # carga e validacao
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path, strict: bool = True) -> tuple["MappingRegistry", list[InvalidMappingError]]:
        """
        Carrega o de-para. Retorna (registry, erros).

        strict=True  -> pontos invalidos sao REJEITADOS (nao entram no registry)
        Isso atende o requisito de "configuracao invalida": o sistema nao sobe
        com um mapeamento quebrado silenciosamente - ele isola o ponto ruim,
        registra o motivo e segue operando os pontos validos.
        """
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        reg = cls(version=str(data.get("mapping_version", "1")))
        erros: list[InvalidMappingError] = []

        for item in data.get("points", []):
            pid = item.get("point_id", "<sem id>")
            try:
                reg.add(cls._build_point(item))
            except InvalidMappingError as e:
                erros.append(e)
                if not strict:
                    raise
        return reg, erros

    @staticmethod
    def _build_point(item: dict) -> PointMapping:
        pid = item.get("point_id")
        if not pid:
            raise InvalidMappingError("<sem id>", "campo obrigatorio 'point_id' ausente")

        for campo in ("source_protocol", "source_address", "asset_id", "measurement"):
            if not item.get(campo):
                raise InvalidMappingError(pid, f"campo obrigatorio '{campo}' ausente")

        tipo_bruto = item.get("data_type", "float")
        try:
            tipo = DataType(tipo_bruto)
        except ValueError:
            raise InvalidMappingError(
                pid, f"data_type '{tipo_bruto}' invalido "
                     f"(esperado: {', '.join(t.value for t in DataType)})")

        try:
            escala = float(item.get("scale", 1.0))
            deslocamento = float(item.get("offset", 0.0))
        except (TypeError, ValueError):
            raise InvalidMappingError(pid, "scale/offset devem ser numericos")

        if escala == 0:
            raise InvalidMappingError(pid, "scale igual a zero zeraria toda leitura deste ponto")

        minimo = item.get("min_value")
        maximo = item.get("max_value")
        if minimo is not None and maximo is not None and float(minimo) >= float(maximo):
            raise InvalidMappingError(pid, f"min_value ({minimo}) >= max_value ({maximo})")

        return PointMapping(
            point_id=pid,
            source_protocol=item["source_protocol"],
            source_device=item.get("source_device", ""),
            source_address=str(item["source_address"]),
            asset_id=item["asset_id"],
            asset_type=item.get("asset_type", "unknown"),
            measurement=item["measurement"],
            iec61850_hint=item.get("iec61850_hint"),
            data_type=tipo,
            unit=item.get("unit", ""),
            scale=escala,
            offset=deslocamento,
            min_value=float(minimo) if minimo is not None else None,
            max_value=float(maximo) if maximo is not None else None,
            target_protocol=item.get("target_protocol", ""),
            target_address=str(item.get("target_address", "")),
            target_scale=float(item.get("target_scale", 1.0)),
            target_offset=float(item.get("target_offset", 0.0)),
            enabled=bool(item.get("enabled", True)),
        )

    # ------------------------------------------------------------------
    # operacoes (base da interface de cadastro exigida pelo desafio)
    # ------------------------------------------------------------------

    def add(self, point: PointMapping) -> None:
        self._points[point.point_id] = point

    def update(self, point_id: str, **campos) -> PointMapping:
        p = self._points[point_id]
        for k, v in campos.items():
            if not hasattr(p, k):
                raise InvalidMappingError(point_id, f"campo desconhecido '{k}'")
            setattr(p, k, v)
        return p

    def remove(self, point_id: str) -> None:
        self._points.pop(point_id, None)

    def get(self, point_id: str) -> PointMapping | None:
        return self._points.get(point_id)

    def all(self) -> list[PointMapping]:
        return [p for p in self._points.values() if p.enabled]

    def by_source_protocol(self, protocol: str) -> list[PointMapping]:
        return [p for p in self.all() if p.source_protocol == protocol]

    def __len__(self) -> int:
        return len(self._points)
