"""
Adaptador de ORIGEM - IEC 61850 MMS (via libiec61850 / pyiec61850).

Toda dependencia de pyiec61850 esta confinada neste arquivo.

IEC 61850 e o protocolo semanticamente RICO do par de demonstracao:
    - modelo hierarquico auto-descritivo (LD -> LN -> DO -> DA)
    - qualidade nativa como bit-string detalhada  (atributo 'q')
    - estampa de tempo nativa por atributo        (atributo 't')
    - Functional Constraints (MX = medida, ST = status, DC = descricao)

Endereco esperado no de-para:
    simpleIOGenericIO/GGIO1.AnIn1.mag.f@MX
    (referencia do objeto @ functional constraint)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# o binding compilado vive junto do build da libiec61850
_BINDING = Path.home() / "vendor" / "libiec61850" / "build" / "pyiec61850"
if _BINDING.exists() and str(_BINDING) not in sys.path:
    sys.path.insert(0, str(_BINDING))

import pyiec61850 as iec  # noqa: E402

from core.canonical import MetadataOrigin, Quality, QualityDetail, Validity  # noqa: E402
from core.ports import RawReading, SourceAdapter  # noqa: E402


_FC = {
    "MX": iec.IEC61850_FC_MX,   # measurands
    "ST": iec.IEC61850_FC_ST,   # status
    "DC": iec.IEC61850_FC_DC,   # description
    "CF": iec.IEC61850_FC_CF,   # configuration
}


class MmsSource(SourceAdapter):
    protocol_name = "iec61850-mms"
    adapter_version = "1.0"

    def __init__(self, host: str = "localhost", port: int = 102):
        self.host = host
        self.port = port
        self._con = None

    def connect(self) -> None:
        self._con = iec.IedConnection_create()
        erro = iec.IedConnection_connect(self._con, self.host, self.port)
        if erro != iec.IED_ERROR_OK:
            iec.IedConnection_destroy(self._con)
            self._con = None
            raise ConnectionError(
                f"falha ao conectar no IED {self.host}:{self.port} (codigo {erro})")

    @property
    def connected(self) -> bool:
        return self._con is not None

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_address(address: str) -> tuple[str, int]:
        if "@" in address:
            ref, fc = address.split("@", 1)
            return ref, _FC.get(fc.upper(), iec.IEC61850_FC_MX)
        return address, iec.IEC61850_FC_MX

    @staticmethod
    def _data_object_ref(ref: str) -> str:
        """
        Sobe da referencia do Data Attribute para o Data Object.

        No 61850, 'q' e 't' sao IRMAOS de 'mag' dentro da Common Data Class MV,
        nao filhos dele:
            GGIO1.AnIn1.mag.f  -> valor
            GGIO1.AnIn1.q      -> qualidade
            GGIO1.AnIn1.t      -> estampa de tempo
        """
        partes = ref.split(".")
        if len(partes) >= 3 and partes[-2] == "mag" and partes[-1] in ("f", "i"):
            return ".".join(partes[:-2])     # remove 'mag.f'
        if len(partes) >= 2:
            return ".".join(partes[:-1])     # remove 'stVal', etc.
        return ref

    # bits 0-1 do atributo Quality do IEC 61850 (ver IEC 61850-7-3)
    _VALIDITY = {
        0: Validity.GOOD,          # QUALITY_VALIDITY_GOOD
        1: Validity.INVALID,       # QUALITY_VALIDITY_INVALID
        2: Validity.INVALID,       # QUALITY_VALIDITY_RESERVED
        3: Validity.QUESTIONABLE,  # QUALITY_VALIDITY_QUESTIONABLE
    }

    _DETAIL_FLAGS = {
        "QUALITY_DETAIL_OVERFLOW": QualityDetail.OVERFLOW,
        "QUALITY_DETAIL_OUT_OF_RANGE": QualityDetail.OUT_OF_RANGE,
        "QUALITY_DETAIL_OLD_DATA": QualityDetail.OLD_DATA,
        "QUALITY_DETAIL_INACCURATE": QualityDetail.INACCURATE,
        "QUALITY_DETAIL_FAILURE": QualityDetail.COMM_LOST,
    }

    def _read_quality(self, ref: str, fc) -> Quality | None:
        """
        Le o atributo 'q' do Data Object.

        No 61850 a qualidade faz parte da Common Data Class - nao e um extra
        opcional do transporte, como acontece nos demais protocolos.

        O binding devolve a Quality ja como inteiro (bit-string), entao a
        decodificacao e feita sobre os bits, nao pelos helpers em C.
        """
        try:
            ref_q = self._data_object_ref(ref) + ".q"
            resultado = iec.IedConnection_readQualityValue(self._con, ref_q, fc)
            if not isinstance(resultado, (tuple, list)) or len(resultado) < 2:
                return None
            bits, erro = resultado[0], resultado[1]
            if erro != iec.IED_ERROR_OK:
                return None

            validade = self._VALIDITY.get(bits & 0b11, Validity.GOOD)

            detalhes = set()
            for nome, destino in self._DETAIL_FLAGS.items():
                mascara = getattr(iec, nome, None)
                if mascara and (bits & mascara):
                    detalhes.add(destino)

            return Quality(validity=validade, detail=frozenset(detalhes),
                           origin=MetadataOrigin.NATIVE)
        except Exception:
            return None

    def _read_timestamp(self, ref: str, fc) -> datetime | None:
        """Le o atributo 't' - o instante REAL de aquisicao registrado no IED."""
        ts = None
        try:
            ref_t = self._data_object_ref(ref) + ".t"
            ts = iec.Timestamp_create()
            resultado = iec.IedConnection_readTimestampValue(self._con, ref_t, fc, ts)

            # o binding pode devolver (timestamp, erro) ou so o erro
            erro = resultado[1] if isinstance(resultado, (tuple, list)) else resultado
            if erro not in (iec.IED_ERROR_OK, None):
                return None

            ms = iec.Timestamp_getTimeInMs(ts)
            if not ms:
                return None
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        except Exception:
            return None
        finally:
            if ts is not None:
                try:
                    iec.Timestamp_destroy(ts)
                except Exception:
                    pass

    # ------------------------------------------------------------------

    def read(self, address: str) -> RawReading:
        if self._con is None:
            return RawReading(address=address, raw_value=None,
                              error="conexao MMS nao estabelecida")

        ref, fc = self._parse_address(address)
        try:
            valor, erro = iec.IedConnection_readFloatValue(self._con, ref, fc)
            if erro != iec.IED_ERROR_OK:
                return RawReading(address=address, raw_value=None,
                                  error=f"falha ao ler {ref} (codigo IED {erro})")

            return RawReading(
                address=address,
                raw_value=valor,
                native_quality=self._read_quality(ref, fc),      # 61850 TEM qualidade
                native_timestamp=self._read_timestamp(ref, fc),  # 61850 TEM timestamp
            )
        except Exception as e:
            return RawReading(address=address, raw_value=None,
                              error=f"perda de comunicacao com IED {self.host}:{self.port} ({e})")

    def close(self) -> None:
        if self._con is not None:
            try:
                iec.IedConnection_close(self._con)
                iec.IedConnection_destroy(self._con)
            finally:
                self._con = None
