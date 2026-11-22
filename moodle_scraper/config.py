"""
Módulo de Configuración Declarativa para Scrapoodle.
Utiliza Pydantic Settings v2 para validación tipada y carga jerárquica de variables.
"""

import configparser
import getpass
import os
from pathlib import Path
from typing import Optional, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScraperConfig(BaseSettings):
    """Clase de configuración central del scraper construida con Pydantic Settings v2."""
    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    user: str = Field(default="", validation_alias="MOODLE_USER")
    password: str = Field(default="", validation_alias="MOODLE_PASSWORD")
    base_url: str = Field(default="https://aulas.itu.uncu.edu.ar/itu/", validation_alias="MOODLE_BASE_URL")
    download_dir: Path = Field(default_factory=lambda: Path("./downloads"), validation_alias="DOWNLOAD_DIR")
    timeout: int = Field(default=30, validation_alias="REQUEST_TIMEOUT")
    max_retries: int = Field(default=3, validation_alias="MAX_RETRIES")
    chunk_size_kb: int = Field(default=8, validation_alias="CHUNK_SIZE_KB")
    overwrite_existing: bool = Field(default=False, validation_alias="OVERWRITE_EXISTING")
    save_section_summaries: bool = Field(default=True, validation_alias="SAVE_SECTION_SUMMARIES")
    save_consolidated_markdown: bool = Field(default=True, validation_alias="SAVE_CONSOLIDATED_MARKDOWN")
    max_workers: int = Field(default=3, validation_alias="MAX_WORKERS")
    request_delay: float = Field(default=0.0, validation_alias="REQUEST_DELAY")
    session_cookie: Optional[str] = Field(default=None, validation_alias="MOODLE_SESSION_COOKIE")

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, v: str) -> str:
        if v and not v.endswith("/"):
            return v + "/"
        return v

    def validate(self) -> List[str]:
        """Valida que los parámetros indispensables estén configurados."""
        errors: List[str] = []
        if not self.base_url or not self.base_url.startswith(("http://", "https://")):
            errors.append("La URL base de Moodle no es válida o está vacía.")
        if not self.session_cookie:
            if not self.user:
                errors.append("El usuario (MOODLE_USER) no está configurado.")
            if not self.password:
                errors.append("La contraseña (MOODLE_PASSWORD) no está configurada.")
        return errors

    @property
    def chunk_size(self) -> int:
        """Tamaño de chunk en bytes para streaming."""
        return self.chunk_size_kb * 1024


def prompt_for_credentials(
    config: ScraperConfig,
    input_func=input,
    password_func=getpass.getpass,
) -> None:
    """Solicita credenciales o cookie de sesión sin mostrarlas en pantalla."""
    if config.session_cookie:
        return

    if not config.user:
        config.user = input_func(
            "Usuario de Moodle (Enter para usar cookie de sesión): "
        ).strip()

    if not config.user:
        config.session_cookie = password_func("Cookie MoodleSession: ").strip()
        return

    if not config.password:
        config.password = password_func("Contraseña de Moodle: ")


def prompt_retry_credentials(
    config: ScraperConfig,
    input_func=input,
    password_func=getpass.getpass,
) -> bool:
    """
    Pregunta al usuario si desea reintentar ingresando nuevamente la contraseña
    (o cambiar de usuario) cuando las credenciales fueron rechazadas por Moodle.
    """
    try:
        choice = input_func(
            "¿Desea reintentar ingresando la contraseña nuevamente? [S/n] ('u' para cambiar usuario): "
        ).strip().lower()
    except (EOFError, OSError):
        return False

    if choice in ("n", "no", "q", "salir", "exit"):
        return False

    if choice in ("u", "user", "usuario"):
        config.user = ""
        config.password = ""
        config.session_cookie = None
        try:
            prompt_for_credentials(config, input_func=input_func, password_func=password_func)
        except (EOFError, OSError):
            return False
        return bool((config.user and config.password) or config.session_cookie)

    if config.user:
        config.password = ""
        config.session_cookie = None
        try:
            config.password = password_func("Contraseña de Moodle: ")
        except (EOFError, OSError):
            return False
        return bool(config.password)

    config.session_cookie = None
    config.password = ""
    try:
        prompt_for_credentials(config, input_func=input_func, password_func=password_func)
    except (EOFError, OSError):
        return False
    return bool((config.user and config.password) or config.session_cookie)


def prompt_for_base_url(config: ScraperConfig, input_func=input) -> None:
    """Solicita la URL base de Moodle cuando no fue configurada."""
    if config.base_url:
        return

    val = input_func("URL base de Moodle: ").strip()
    if not val:
        val = "https://aulas.itu.uncu.edu.ar/itu/"
    if val and not val.endswith("/"):
        val += "/"
    config.base_url = val


def _read_ini_file(path: Path) -> dict:
    """Lee un archivo de configuración estilo INI."""
    parser = configparser.ConfigParser()
    try:
        parser.read(str(path), encoding="utf-8")
        result = {}
        for section in parser.sections():
            for key, val in parser.items(section):
                result[key.lower()] = val
        return result
    except Exception:
        return {}


def load_config(
    env_path: Optional[str | Path] = None,
    conf_path: Optional[str | Path] = None
) -> ScraperConfig:
    """
    Carga la configuración combinando .env (via pydantic-settings), archivos INI y variables del sistema.
    """
    env_file = Path(env_path) if env_path else (Path(".env") if Path(".env").exists() else None)

    # Valores desde archivo INI si existe
    conf_values = {}
    if conf_path is not None and Path(conf_path).exists():
        conf_values = _read_ini_file(Path(conf_path))
    elif conf_path is None:
        for candidate in [Path("scraper.conf"), Path("config.ini"), Path("moodle.conf")]:
            if candidate.exists():
                conf_values = _read_ini_file(candidate)
                break

    init_kwargs = {}
    if env_file and env_file.exists():
        init_kwargs["_env_file"] = str(env_file)
    else:
        init_kwargs["_env_file"] = None

    # Mapear conf_values si existen
    for key, val in conf_values.items():
        if key in ScraperConfig.model_fields and val is not None:
            init_kwargs[key] = val

    if "base_url" not in conf_values and "MOODLE_BASE_URL" not in os.environ:
        if not env_file or not env_file.exists():
            init_kwargs["base_url"] = ""
        else:
            try:
                content = env_file.read_text(encoding="utf-8")
                if "MOODLE_BASE_URL" not in content:
                    init_kwargs["base_url"] = ""
            except Exception:
                init_kwargs["base_url"] = ""

    # Instanciar Pydantic Settings
    # Las variables de entorno de os.environ y _env_file tienen precedencia
    try:
        config = ScraperConfig(**init_kwargs)
    except Exception:
        # Fallback si ocurre algún tipo de validación estricta
        config = ScraperConfig()

    # Ignorar placeholders de .env.example para solicitar credenciales reales al inicio
    if config.user in ("tu_usuario_aqui", "tu_usuario"):
        config.user = ""
    if config.password in ("tu_contraseña_aqui", "tu_contrasena_aqui", "tu_password_aqui"):
        config.password = ""
    if config.session_cookie in ("tu_cookie_moodlesession_aqui", ""):
        config.session_cookie = None

    return config
