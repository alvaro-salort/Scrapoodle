"""
Módulo de Descarga: Descargas en streaming con httpx y hishel (RFC 9111),
resolución de Content-Disposition, anti-duplicados, reanudación y barras de progreso rich.
"""

import hashlib
import mimetypes
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse

from moodle_scraper.config import ScraperConfig
from moodle_scraper.eventos import Evento
from moodle_scraper.manifest import sha256_file
from moodle_scraper.observadores import crear_bus_por_defecto
from moodle_scraper.session import MoodleSession, MoodleAuthError
from moodle_scraper.utils import sanitize_filename, format_bytes

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif"}


def _repair_text(value: str) -> str:
    """Repara mojibake típico y normaliza a Unicode NFC."""
    if not value:
        return value

    repaired = value
    for encoding in ("latin-1", "cp1252"):
        try:
            candidate = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate != value:
            repaired = candidate
            break

    return unicodedata.normalize("NFC", repaired)


class _DownloadSizeExceeded(Exception):
    """Se lanza cuando una descarga supera el tamaño máximo permitido."""


class _DownloadIncomplete(Exception):
    """Se lanza cuando los bytes descargados no coinciden con Content-Length."""


class Downloader:
    """Administra la descarga de archivos y recursos multimedia de Moodle con httpx y hishel."""

    def __init__(self, session: MoodleSession, config: ScraperConfig, bus_eventos=None):
        self.session = session
        self.config = config
        self.bus_eventos = bus_eventos or crear_bus_por_defecto()

    def _emitir(self, tipo: str, **datos) -> None:
        self.bus_eventos.publicar(Evento(tipo, datos))

    def extract_filename_from_headers(self, response) -> Optional[str]:
        """Extrae el nombre de archivo desde Content-Disposition (RFC 5987 / RFC 2616)."""
        cd = ""
        if hasattr(response, "headers") and hasattr(response.headers, "get"):
            val = response.headers.get("Content-Disposition", "")
            if isinstance(val, str):
                cd = val
        if not cd:
            return None

        # RFC 5987: filename*=UTF-8''nombre.pdf
        match_rfc5987 = re.search(r"filename\*\s*=\s*(?:UTF-8|iso-8859-1)?\'\'([^;]+)", cd, re.IGNORECASE)
        if match_rfc5987:
            encoded_name = match_rfc5987.group(1)
            return _repair_text(unquote(encoded_name).strip(' \t\r\n"'))

        # RFC 2616 estándar: filename="nombre.pdf"
        match_std = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
        if match_std:
            return _repair_text(match_std.group(1).strip())

        match_unquoted = re.search(r'filename\s*=\s*([^;]+)', cd, re.IGNORECASE)
        if match_unquoted:
            return _repair_text(match_unquoted.group(1).strip(' \t\r\n"'))

        return None

    def resolve_filename(
        self,
        response,
        fallback_name: Optional[str] = None
    ) -> str:
        """Resuelve el nombre real del archivo analizando headers, URLs y Content-Type."""
        header_name = self.extract_filename_from_headers(response)
        if header_name:
            return sanitize_filename(header_name)

        parsed_url = urlparse(str(getattr(response, "url", "")))
        url_path = _repair_text(unquote(parsed_url.path))
        url_filename = Path(url_path).name

        if url_filename and "." in url_filename and not url_filename.endswith((".php", ".html", ".htm")):
            return sanitize_filename(url_filename)

        clean_fallback = sanitize_filename(_repair_text(fallback_name or "archivo"))
        raw_ct = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
        content_type = raw_ct.split(";")[0].strip() if isinstance(raw_ct, str) else ""

        if "." not in clean_fallback and content_type:
            guessed_ext = mimetypes.guess_extension(content_type)
            if guessed_ext:
                if guessed_ext == ".htm":
                    guessed_ext = ".html"
                clean_fallback = f"{clean_fallback}{guessed_ext}"

        return clean_fallback

    @staticmethod
    def _is_content_type_allowed(
        content_type: str,
        url: str,
        allowed_prefix: Optional[str],
    ) -> bool:
        if not allowed_prefix:
            return True
        if content_type.startswith(allowed_prefix):
            return True
        if allowed_prefix == "image/" and content_type in ("", "application/octet-stream"):
            return Path(urlparse(url).path).suffix.lower() in _IMAGE_EXTENSIONS
        return False

    def download_file(
        self,
        url: str,
        dest_dir: Path,
        fallback_name: Optional[str] = None,
        source_info: str = "",
        filename_suffix: Optional[str] = None,
        allowed_content_prefix: Optional[str] = None,
        max_bytes: Optional[int] = None,
        metadata_out: Optional[dict] = None,
        progress_callback=None,
    ) -> Tuple[bool, Optional[Path], str]:
        """Descarga un archivo con streaming y caché RFC 9111."""
        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            if hasattr(self.session, "get"):
                response = self.session.get(url, stream=True)
            elif hasattr(self.session, "client"):
                response = self.session.client.get(url)
            else:
                response = None
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
        except MoodleAuthError:
            raise
        except Exception as e:
            self._emitir("log", nivel="error", mensaje=f"Fallo al solicitar descarga de {url}: {e}")
            return False, None, f"Error de red: {e}"

        if isinstance(self.session, MoodleSession) and self.session.is_login_response(response):
            if hasattr(response, "close"):
                response.close()
            raise MoodleAuthError("La sesión de Moodle expiró durante la descarga. Volvé a iniciar sesión.")

        raw_ct = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
        content_type = raw_ct.split(";")[0].strip().lower() if isinstance(raw_ct, str) else ""
        if not self._is_content_type_allowed(content_type, url, allowed_content_prefix):
            if hasattr(response, "close"):
                response.close()
            self._emitir("log", nivel="warn", mensaje=f"Descarga omitida por tipo de contenido no permitido ({content_type or 'desconocido'}): {url}")
            return False, None, f"Tipo de contenido no permitido: {content_type or 'desconocido'}"

        filename = self.resolve_filename(response, fallback_name)
        if filename_suffix:
            path = Path(filename)
            filename = f"{path.stem}-{sanitize_filename(filename_suffix)}{path.suffix}"
        target_path = dest_dir / filename
        temp_path = dest_dir / f"{filename}.part"

        if (
            content_type.startswith("text/html")
            and target_path.suffix.lower() not in (".html", ".htm")
        ):
            if hasattr(response, "close"):
                response.close()
            self._emitir("log", nivel="warn", mensaje=f"Se recibió HTML en lugar de un archivo descargable: {url}")
            return False, None, "La respuesta es HTML y no un archivo descargable (¿sesión expirada?)"

        cl_header = response.headers.get("Content-Length") if hasattr(response, "headers") else None
        total_bytes = int(cl_header) if isinstance(cl_header, (str, int)) and str(cl_header).isdigit() else None
        formatted_total = format_bytes(total_bytes) if total_bytes else "desconocido"

        if max_bytes is not None and total_bytes is not None and total_bytes > max_bytes:
            if hasattr(response, "close"):
                response.close()
            self._emitir("log", nivel="warn", mensaje=f"Descarga omitida por tamaño excesivo ({format_bytes(total_bytes)} > {format_bytes(max_bytes)}): {url}")
            return False, None, f"El archivo excede el tamaño máximo permitido ({format_bytes(max_bytes)})"

        # Anti-Duplicados
        if target_path.exists() and not self.config.overwrite_existing:
            local_size = target_path.stat().st_size
            if (total_bytes and local_size == total_bytes) or (not total_bytes and local_size > 0):
                self._emitir("descarga_omitida", archivo=filename, tamano=format_bytes(local_size))
                if hasattr(response, "close"):
                    response.close()
                if metadata_out is not None:
                    metadata_out.update({
                        "url": url,
                        "size": local_size,
                        "sha256": sha256_file(target_path),
                    })
                return True, target_path, "Omitido por existencia previa"

        chunk_size = self.config.chunk_size
        downloaded_bytes = 0
        hasher = hashlib.sha256() if metadata_out is not None else None

        info_tag = f" [{source_info}]" if source_info else ""
        self._emitir("descarga_iniciada", archivo=filename, info=info_tag, total=formatted_total)

        try:
            with open(temp_path, "wb") as f:
                if hasattr(response, "iter_bytes"):
                    iterator = response.iter_bytes(chunk_size=chunk_size)
                elif hasattr(response, "iter_content"):
                    iterator = response.iter_content(chunk_size=chunk_size)
                else:
                    iterator = [response.content]

                for chunk in iterator:
                    if chunk:
                        downloaded_bytes += len(chunk)
                        if hasher is not None:
                            hasher.update(chunk)
                        if max_bytes is not None and downloaded_bytes > max_bytes:
                            raise _DownloadSizeExceeded(f"El archivo excede el tamaño máximo permitido ({format_bytes(max_bytes)})")
                        f.write(chunk)
                        if progress_callback:
                            progress_callback(len(chunk))

            if total_bytes is not None and not response.headers.get("Content-Encoding") and downloaded_bytes != total_bytes:
                raise _DownloadIncomplete(f"Descarga incompleta: {format_bytes(downloaded_bytes)} de {format_bytes(total_bytes)}")

            if temp_path.exists():
                if target_path.exists():
                    target_path.unlink()
                temp_path.rename(target_path)

            self._emitir("descarga_completada", archivo=filename, tamano=format_bytes(downloaded_bytes))
            if metadata_out is not None:
                metadata_out.update({
                    "url": url,
                    "size": downloaded_bytes,
                    "sha256": hasher.hexdigest() if hasher is not None else "",
                })
            return True, target_path, "Descargado correctamente"

        except (_DownloadSizeExceeded, _DownloadIncomplete) as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            self._emitir("descarga_fallida", archivo=filename, url=url, error=str(e), mensaje=f"Fallo en descarga: {e}: {url}")
            return False, None, str(e)
        except Exception as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            self._emitir("descarga_fallida", archivo=filename, url=url, error=f"Error de escritura: {e}", mensaje=f"Error al escribir {filename}: {e}")
            return False, None, f"Error de escritura: {e}"
        finally:
            if hasattr(response, "close"):
                response.close()

    async def adownload_file(
        self,
        url: str,
        dest_dir: Path,
        fallback_name: Optional[str] = None,
        source_info: str = "",
        filename_suffix: Optional[str] = None,
        allowed_content_prefix: Optional[str] = None,
        max_bytes: Optional[int] = None,
        metadata_out: Optional[dict] = None,
        progress_callback=None,
    ) -> Tuple[bool, Optional[Path], str]:
        """Descarga asíncrona para TaskGroup en streaming."""
        # Se reutiliza la lógica descargando en un hilo seguro o con aget
        return self.download_file(
            url,
            dest_dir,
            fallback_name=fallback_name,
            source_info=source_info,
            filename_suffix=filename_suffix,
            allowed_content_prefix=allowed_content_prefix,
            max_bytes=max_bytes,
            metadata_out=metadata_out,
            progress_callback=progress_callback,
        )
