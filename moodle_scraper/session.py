"""
Gestor de Sesión y Autenticación para Moodle 4.x (ITU UNCUYO).
Implementado con httpx + hishel para gestión automática de caché RFC 9111 (ETag, 304 Not Modified).
"""

import functools
import re
import ssl
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import hishel
from hishel.httpx import CacheClient, AsyncCacheClient
from selectolax.lexbor import LexborHTMLParser

from moodle_scraper.config import ScraperConfig
from moodle_scraper.utils import Logger


@functools.lru_cache(maxsize=1)
def _get_ssl_context() -> ssl.SSLContext:
    """Crea y reutiliza el contexto SSL por defecto para evitar recargar el almacén de certificados en cada instancia."""
    return ssl.create_default_context()


class MoodleAuthError(Exception):
    """Excepción lanzada cuando la autenticación en Moodle falla."""

    def __init__(self, message: str, *, invalid_credentials: bool = False):
        super().__init__(message)
        self.invalid_credentials = invalid_credentials


class _MoodleSqliteStorage(hishel.SqliteStorage):
    """Almacenamiento SQLite RFC 9111 que excluye estrictamente endpoints de login/autenticación."""

    def get_entries(self, key: str):
        if MoodleSession.is_login_url(key):
            return []
        entries = super().get_entries(key)
        return [
            e for e in entries
            if not MoodleSession.is_login_url(getattr(e.request, "url", ""))
        ]

    def create_entry(self, request, response, key: str, *, ttl=None, tti=None):
        if MoodleSession.is_login_url(getattr(request, "url", "")):
            return hishel.Entry(id=uuid.uuid4(), request=request, response=response)
        return super().create_entry(request, response, key, ttl=ttl, tti=tti)


class _AsyncThreadSqliteStorage(hishel.AsyncBaseStorage):
    """Adaptador asíncrono para SqliteStorage cuando anysqlite no está instalado."""

    def __init__(self, sync_storage: hishel.SqliteStorage):
        super().__init__()
        self._sync = sync_storage

    async def create_entry(self, request, response, key, *, ttl=None, tti=None):
        import asyncio
        return await asyncio.to_thread(
            self._sync.create_entry, request, response, key, ttl=ttl, tti=tti
        )

    async def get_entries(self, key):
        import asyncio
        return await asyncio.to_thread(self._sync.get_entries, key)

    async def remove_entry(self, id):
        import asyncio
        return await asyncio.to_thread(self._sync.remove_entry, id)

    async def update_headers(self, id, new_headers):
        import asyncio
        return await asyncio.to_thread(self._sync.update_headers, id, new_headers)

    async def close(self):
        import asyncio
        await asyncio.to_thread(self._sync.close)


class MoodleSession:
    """Administra la sesión HTTP, autenticación y llamadas a la API de Moodle con httpx y hishel."""

    def __init__(self, config: ScraperConfig):
        self.config = config
        self.sesskey: Optional[str] = None
        self.is_authenticated: bool = False
        self.user_fullname: Optional[str] = None

        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Connection": "keep-alive"
        }

        # Configurar cookies iniciales si hay session_cookie
        cookies = httpx.Cookies()
        if self.config.session_cookie:
            parsed_base = urlparse(self.config.base_url)
            hostname = parsed_base.hostname or ""
            cookies.set("MoodleSession", self.config.session_cookie, domain=hostname, path="/")

        # Configuración de almacenamiento SQLite para RFC 9111
        cache_dir = Path(".cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Eliminar caché antigua sin RFC si existiera para evitar tokens CSRF o sesiones cacheadas
        legacy_cache = cache_dir / "moodle_cache.sqlite"
        if legacy_cache.exists():
            try:
                legacy_cache.unlink()
            except OSError:
                pass

        cache_db_path = str(cache_dir / "moodle_cache_rfc.sqlite")
        self._sync_storage = _MoodleSqliteStorage(database_path=cache_db_path)
        self._cache_db_path = cache_db_path
        self._timeout = httpx.Timeout(float(config.timeout), connect=10.0)
        self._async_client: Optional[AsyncCacheClient] = None

        # Cliente sincronizado con Hishel en modo estricto RFC 9111 (rfc=True)
        self.client = CacheClient(
            storage=self._sync_storage,
            rfc=True,
            headers=self._headers,
            cookies=cookies,
            timeout=self._timeout,
            follow_redirects=True,
            verify=_get_ssl_context(),
        )

        # Alias session para compatibilidad con código existente
        self.session = self.client

        def _prepare_request(req):
            method = getattr(req, "method", "GET")
            url = getattr(req, "url", str(req))
            return self.client.build_request(method, url)

        self.client.prepare_request = _prepare_request

    @property
    def async_client(self) -> AsyncCacheClient:
        """Inicializa perezosamente el cliente asíncrono cuando se requiere."""
        if self._async_client is None:
            try:
                async_storage = hishel.AsyncSqliteStorage(database_path=self._cache_db_path)
            except (ImportError, Exception):
                async_storage = _AsyncThreadSqliteStorage(self._sync_storage)
            self._async_client = AsyncCacheClient(
                storage=async_storage,
                rfc=True,
                headers=self._headers,
                cookies=self.client.cookies,
                timeout=self._timeout,
                follow_redirects=True,
                verify=_get_ssl_context(),
            )
        return self._async_client

    def close(self) -> None:
        """Cierra los clientes HTTP."""
        try:
            self.client.close()
        except Exception:
            pass

    @staticmethod
    def is_login_url(url: str) -> bool:
        """Indica si una URL corresponde a la página de login de Moodle."""
        path = urlparse(str(url) if url else "").path.lower().rstrip("/")
        return path == "/login/index.php" or path.endswith("/login/index.php")

    @staticmethod
    def is_invalid_credentials_error(error: BaseException) -> bool:
        """Indica si un error de autenticación se debe a credenciales inválidas o rechazadas."""
        if getattr(error, "invalid_credentials", False):
            return True
        msg = str(error).lower()
        if any(net in msg for net in ("no se pudo conectar", "error de red")):
            return False
        return any(
            kw in msg
            for kw in (
                "acceso inválido",
                "acceso invalido",
                "invalid login",
                "credenciales fueron rechazadas",
                "error de autenticación reportado por moodle",
                "error de autenticacion reportado por moodle",
                "cookie de sesión",
                "cookie de sesion",
                "credenciales incompletas",
            )
        )

    def is_login_response(self, response: httpx.Response) -> bool:
        """Indica si la respuesta terminó en la página de login (sesión expirada)."""
        return self.is_login_url(str(getattr(response, "url", "")))

    def _resolve_url(self, endpoint: str) -> str:
        """Construye una URL absoluta a partir de un endpoint relativo."""
        return urljoin(self.config.base_url, endpoint)

    def extract_logintoken(self) -> Tuple[str, str]:
        """Obtiene el token CSRF (logintoken) y anchor desde login/index.php."""
        login_url = self._resolve_url("login/index.php")
        Logger.info(f"Obteniendo página de login y token CSRF: {login_url}")

        try:
            resp = self.client.get(login_url)
            resp.raise_for_status()
        except Exception as e:
            raise MoodleAuthError(f"No se pudo conectar a la página de login de Moodle: {e}")

        tree = LexborHTMLParser(resp.text)
        token_input = tree.css_first("input[name='logintoken']")
        logintoken = (token_input.attributes.get("value") or "") if token_input else ""

        anchor_input = tree.css_first("input[name='anchor']")
        anchor = (anchor_input.attributes.get("value") or "") if anchor_input else ""

        if not logintoken:
            match = re.search(r'name=["\']logintoken["\']\s+value=["\']([^"\']+)["\']', resp.text)
            if match:
                logintoken = match.group(1)

        if not logintoken:
            Logger.warn("No se encontró 'logintoken' en el formulario de login. Continuando sin él.")

        return logintoken, anchor

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """Realiza la autenticación en Moodle 4.x con soporte de credenciales o cookie."""
        self.is_authenticated = False
        self.sesskey = None
        self.user_fullname = None

        if self.config.session_cookie and not username and not password:
            parsed_base = urlparse(self.config.base_url)
            hostname = parsed_base.hostname or ""
            self.client.cookies.set("MoodleSession", self.config.session_cookie, domain=hostname, path="/")
            Logger.info("Validando autenticación con cookie MoodleSession...")
            try:
                resp = self.get("my/")
            except Exception as e:
                raise MoodleAuthError(f"Error de red validando cookie de sesión: {e}")

            if "login/index.php" in str(resp.url):
                raise MoodleAuthError(
                    "La cookie de sesión (MOODLE_SESSION_COOKIE) es inválida o ha expirado.",
                    invalid_credentials=True,
                )

            tree = LexborHTMLParser(resp.text)
            self.sesskey = self._extract_sesskey(resp.text)
            self.user_fullname = self._extract_user_fullname(tree)
            self.is_authenticated = True

            user_disp = self.user_fullname or "Sesión SSO"
            Logger.success(f"Autenticación exitosa mediante cookie! Conectado como: {user_disp}")
            if self.sesskey:
                Logger.info("Clave de sesión (sesskey) obtenida")
            return True

        user = username or self.config.user
        pwd = password or self.config.password

        if not user or not pwd:
            raise MoodleAuthError(
                "Credenciales incompletas. Configure MOODLE_USER y MOODLE_PASSWORD.",
                invalid_credentials=True,
            )

        # Limpiar cookies previas antes de negociar un nuevo logintoken CSRF y MoodleSession
        self.client.cookies.clear()

        logintoken, anchor = self.extract_logintoken()
        login_post_url = self._resolve_url("login/index.php")
        payload = {
            "anchor": anchor,
            "logintoken": logintoken,
            "username": user,
            "password": pwd,
            "rememberusername": "1"
        }

        Logger.info(f"Autenticando usuario '{user}' en Moodle...")
        try:
            resp = self.client.post(login_post_url, data=payload)
            resp.raise_for_status()
        except Exception as e:
            raise MoodleAuthError(f"Error de red durante el intento de login: {e}")

        tree = LexborHTMLParser(resp.text)
        error_elem = None
        for sel in ("#loginerrormessage", ".alert-danger", ".notifyproblem", "span.error"):
            cand = tree.css_first(sel)
            if cand and cand.text(strip=True):
                error_elem = cand
                break
        if error_elem:
            error_text = error_elem.text(strip=True)
            raise MoodleAuthError(
                f"Error de autenticación reportado por Moodle: {error_text}",
                invalid_credentials=True,
            )

        if "login/index.php" in str(resp.url) and tree.css_first("input[name='password']"):
            raise MoodleAuthError(
                "Fallo de autenticación: Las credenciales fueron rechazadas por Moodle.",
                invalid_credentials=True,
            )

        self.sesskey = self._extract_sesskey(resp.text)
        self.user_fullname = self._extract_user_fullname(tree)
        self.is_authenticated = True

        Logger.success(f"Autenticación exitosa! Sesión iniciada como: {self.user_fullname or user}")
        if self.sesskey:
            Logger.info("Clave de sesión (sesskey) obtenida")

        return True

    def _extract_sesskey(self, html_content: str) -> Optional[str]:
        """Extrae el sesskey desde configuraciones de Javascript o HTML."""
        match = re.search(r'["\']?sesskey["\']?\s*[:=]\s*["\']([a-zA-Z0-9_-]+)["\']', html_content)
        if match:
            return match.group(1)
        match = re.search(r'[?&]sesskey=([a-zA-Z0-9_-]+)', html_content)
        if match:
            return match.group(1)
        match = re.search(r'<input[^>]+name=["\']sesskey["\'][^>]+value=["\']([a-zA-Z0-9_-]+)["\']', html_content)
        if match:
            return match.group(1)
        return None

    def _extract_user_fullname(self, tree: LexborHTMLParser | str) -> Optional[str]:
        """Extrae el nombre completo del usuario conectado desde el navbar o pie de sesión."""
        if isinstance(tree, str):
            tree = LexborHTMLParser(tree)
        for sel in (".usertext", ".userbutton", ".logininfo a[href*='user/profile.php']"):
            elem = tree.css_first(sel)
            if elem:
                text = elem.text(strip=True)
                if text:
                    return text

        user_menu = tree.css_first(".usermenu")
        if user_menu:
            menu_tree = LexborHTMLParser(user_menu.html or "")
            for drop in menu_tree.css(".dropdown-menu, #user-action-menu, .sr-only, .accesshide"):
                drop.decompose()
            text = menu_tree.text(strip=True)
            if text:
                return text
        return None

    @staticmethod
    def _normalize_httpx_kwargs(kwargs: dict) -> dict:
        """Adapta argumentos heredados de requests (stream, allow_redirects) a httpx."""
        kwargs.pop("stream", None)
        if "allow_redirects" in kwargs:
            kwargs["follow_redirects"] = kwargs.pop("allow_redirects")
        return kwargs

    def get(self, url_or_endpoint: str, **kwargs) -> httpx.Response:
        """Petición GET sincronizada con caché RFC 9111 y detección de sesión expirada."""
        if self.config.request_delay > 0:
            time.sleep(self.config.request_delay)
        target_url = self._resolve_url(url_or_endpoint)
        self._normalize_httpx_kwargs(kwargs)
        response = self.client.get(target_url, **kwargs)
        if self.is_authenticated and self.is_login_response(response):
            response.close()
            raise MoodleAuthError("La sesión de Moodle expiró o fue invalidada. Volvé a iniciar sesión.")
        return response

    def post(self, url_or_endpoint: str, **kwargs) -> httpx.Response:
        """Petición POST sincronizada con detección de sesión expirada."""
        if self.config.request_delay > 0:
            time.sleep(self.config.request_delay)
        target_url = self._resolve_url(url_or_endpoint)
        self._normalize_httpx_kwargs(kwargs)
        response = self.client.post(target_url, **kwargs)
        if self.is_authenticated and self.is_login_response(response):
            response.close()
            raise MoodleAuthError("La sesión de Moodle expiró o fue invalidada. Volvé a iniciar sesión.")
        return response

    async def aget(self, url_or_endpoint: str, **kwargs) -> httpx.Response:
        """Petición GET asíncrona para TaskGroup."""
        target_url = self._resolve_url(url_or_endpoint)
        self._normalize_httpx_kwargs(kwargs)
        response = await self.async_client.get(target_url, **kwargs)
        if self.is_authenticated and self.is_login_response(response):
            await response.aclose()
            raise MoodleAuthError("La sesión de Moodle expiró o fue invalidada. Volvé a iniciar sesión.")
        return response

    async def apost(self, url_or_endpoint: str, **kwargs) -> httpx.Response:
        """Petición POST asíncrona para TaskGroup."""
        target_url = self._resolve_url(url_or_endpoint)
        self._normalize_httpx_kwargs(kwargs)
        response = await self.async_client.post(target_url, **kwargs)
        if self.is_authenticated and self.is_login_response(response):
            await response.aclose()
            raise MoodleAuthError("La sesión de Moodle expiró o fue invalidada. Volvé a iniciar sesión.")
        return response

