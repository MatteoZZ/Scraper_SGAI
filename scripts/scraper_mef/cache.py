"""Skip a 3 livelli: locale (PDF valido), server/archivio (liste nomi), embedding flag."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .download import local_pdf_ok, validate_local_pdf


def _norm_base(name: str) -> str:
    text = (name or "").strip()
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    return text.lower()


@dataclass
class SkipDecision:
    action: str
    layer: str
    detail: str

    def to_dict(self) -> dict:
        return {"action": self.action, "layer": self.layer, "detail": self.detail}


class SkipIndex:
    """
    Ordine di valutazione:
      1) locale  → PDF valido già in output_dir (size/firma/EOF)
      2) server  → nome in cache / |embedded
      3) altrimenti → would_download
    File locali corrotti/vuoti/HTML non fanno skip_local (consentono ridownload).
    """

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        server_cache_files: list[Path] | None = None,
        embedded_files: list[Path] | None = None,
        min_pdf_bytes: int = 1000,
        max_pdf_bytes: int = 80_000_000,
    ) -> None:
        self.output_dir = output_dir
        self.server_cache_files = server_cache_files or []
        self.embedded_files = embedded_files or []
        self.min_pdf_bytes = int(min_pdf_bytes)
        self.max_pdf_bytes = int(max_pdf_bytes)
        self.server_keys: set[str] = set()
        self.embedded_keys: set[str] = set()
        self.sources: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.server_keys.clear()
        self.embedded_keys.clear()
        self.sources = []
        all_files = list(self.server_cache_files) + list(self.embedded_files)
        seen_files: set[str] = set()
        for path in all_files:
            keyp = str(path)
            if keyp in seen_files:
                continue
            seen_files.add(keyp)
            if not path.exists():
                self.sources.append(f"missing:{path}")
                continue
            n_server = 0
            n_emb = 0
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                if "|embedded" in raw.lower():
                    base = _norm_base(raw.split("|", 1)[0])
                    self.embedded_keys.add(base)
                    self.server_keys.add(base)
                    n_emb += 1
                    n_server += 1
                else:
                    self.server_keys.add(_norm_base(raw))
                    n_server += 1
            self.sources.append(f"cache:{path.name} server={n_server} embedded={n_emb}")

        if self.output_dir:
            self.sources.append(f"local_dir:{self.output_dir}")

    def local_path(self, nome_base: str) -> Path | None:
        if not self.output_dir:
            return None
        return self.output_dir / f"{nome_base}.pdf"

    def local_errors(self, nome_base: str) -> list[str]:
        path = self.local_path(nome_base)
        if not path:
            return ["no output_dir"]
        if not path.exists():
            return ["non esiste"]
        return validate_local_pdf(
            path, min_bytes=self.min_pdf_bytes, max_bytes=self.max_pdf_bytes
        )

    def is_local(self, nome_base: str) -> bool:
        path = self.local_path(nome_base)
        if not path or not path.exists():
            return False
        return local_pdf_ok(
            path, min_bytes=self.min_pdf_bytes, max_bytes=self.max_pdf_bytes
        )

    def is_server(self, nome_base: str) -> bool:
        return _norm_base(nome_base) in self.server_keys

    def is_embedded(self, nome_base: str) -> bool:
        return _norm_base(nome_base) in self.embedded_keys

    def decide(self, nome_base: str) -> SkipDecision:
        base = nome_base if not nome_base.lower().endswith(".pdf") else nome_base[:-4]
        path = self.local_path(base)
        if path and path.exists():
            errs = self.local_errors(base)
            if not errs:
                return SkipDecision(
                    action="skip_local",
                    layer="A_locale",
                    detail=str(path),
                )
            # file presente ma invalido → non skip; segnala per ridownload
            return SkipDecision(
                action="would_download",
                layer="A_locale_corrupt",
                detail=f"locale invalido ({'; '.join(errs)}): {path}",
            )
        if self.is_embedded(base):
            return SkipDecision(
                action="skip_embedded",
                layer="B_server_embedded",
                detail="presente in lista |embedded",
            )
        if self.is_server(base):
            return SkipDecision(
                action="skip_server",
                layer="B_server_or_D",
                detail="presente in cache nomi / skip D:",
            )
        return SkipDecision(
            action="would_download",
            layer="C_manca",
            detail="non in locale né in cache server/D:",
        )


# Retrocompatibilità col dry-run precedente
class NameCache(SkipIndex):
    def __init__(self, keys_path: Path) -> None:
        super().__init__(server_cache_files=[keys_path], embedded_files=[keys_path])

    def has(self, nome_base: str) -> bool:
        return self.is_server(nome_base)

    def should_skip(self, nome_base: str) -> bool:
        return self.is_embedded(nome_base)
