"""
Pruebas unitarias para concurrencia, retardo de solicitudes y autenticación por cookie de sesión.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from moodle_scraper.config import ScraperConfig
from moodle_scraper.session import MoodleSession, MoodleAuthError
from moodle_scraper.course_scraper import CourseScraper, _DownloadJob, CourseProcessingStats
from moodle_scraper.parser import CourseModule


def test_config_defaults_and_validation():
    cfg = ScraperConfig(session_cookie="abc123xyz")
    errors = cfg.validate()
    assert errors == []
    assert cfg.max_workers == 3
    assert cfg.request_delay == 0.0


def test_session_cookie_login_success():
    cfg = ScraperConfig(session_cookie="valid_cookie_123")
    session = MoodleSession(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://aulas.itu.uncu.edu.ar/itu/my/"
    mock_resp.text = """
    <html>
        <div class="userbutton">Juan Perez</div>
        <script>var M = {cfg: {sesskey: "test_sesskey_999"}};</script>
    </html>
    """

    with patch.object(session.session, "get", return_value=mock_resp):
        success = session.login()
        assert success is True
        assert session.is_authenticated is True
        assert session.sesskey == "test_sesskey_999"
        assert session.user_fullname == "Juan Perez"


def test_session_cookie_is_scoped_to_moodle_host():
    cfg = ScraperConfig(
        base_url="https://moodle.test/moodle/",
        session_cookie="secreto",
    )
    session = MoodleSession(cfg)

    same_host = session.session.prepare_request(
        requests.Request("GET", "https://moodle.test/moodle/my/")
    )
    other_host = session.session.prepare_request(
        requests.Request("GET", "https://evil.test/x.png")
    )

    assert "MoodleSession=secreto" in same_host.headers.get("Cookie", "")
    assert "MoodleSession" not in other_host.headers.get("Cookie", "")


def test_login_does_not_log_sesskey(monkeypatch):
    cfg = ScraperConfig(
        base_url="https://moodle.test/moodle/",
        session_cookie="valida",
    )
    session = MoodleSession(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://moodle.test/moodle/my/"
    mock_resp.text = """
    <html>
        <div class="userbutton">Juan Perez</div>
        <script>var M = {cfg: {sesskey: "sesskey_secreto_999"}};</script>
    </html>
    """
    logged = []
    monkeypatch.setattr(
        "moodle_scraper.session.Logger.info",
        lambda message: logged.append(message),
    )

    with patch.object(session.session, "get", return_value=mock_resp):
        session.login()

    assert session.sesskey == "sesskey_secreto_999"
    assert all("sesskey_secreto_999" not in message for message in logged)


def test_authenticated_request_detects_login_redirect():
    cfg = ScraperConfig(
        base_url="https://moodle.test/moodle/",
        session_cookie="expirada",
    )
    session = MoodleSession(cfg)
    session.is_authenticated = True
    mock_resp = MagicMock()
    mock_resp.url = "https://moodle.test/moodle/login/index.php"

    with patch.object(session.session, "get", return_value=mock_resp):
        with pytest.raises(MoodleAuthError):
            session.get("course/view.php?id=1")

    mock_resp.close.assert_called_once()


def test_session_cookie_login_failure():
    cfg = ScraperConfig(session_cookie="expired_cookie_123")
    session = MoodleSession(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://aulas.itu.uncu.edu.ar/itu/login/index.php"
    mock_resp.text = "<html>Login</html>"

    with patch.object(session.session, "get", return_value=mock_resp):
        with pytest.raises(MoodleAuthError):
            session.login()


def test_concurrent_download_execution(tmp_path):
    cfg = ScraperConfig(max_workers=4)
    session = MoodleSession(cfg)
    scraper = CourseScraper(session, cfg)

    stats = CourseProcessingStats()
    mod = CourseModule(id="1", mod_type="resource", name="Doc1.pdf")

    with patch.object(
        scraper.downloader,
        "download_file",
        return_value=(True, tmp_path / "Doc1.pdf", "Descargado correctamente"),
    ):
        jobs = [
            _DownloadJob(
                url=f"https://example.com/file{i}.pdf",
                dest_dir=tmp_path,
                fallback_name=f"file{i}.pdf",
                source_info=f"Recurso {i}",
                target_module=mod,
                is_direct_resource=True,
                section_name="Tema 1",
            )
            for i in range(5)
        ]

        scraper._execute_download_jobs(jobs, stats)
        assert stats.files_downloaded == 5
        assert stats.files_failed == 0
        assert len(mod.files) == 1


def test_login_never_caches_logintoken_and_allows_retry(tmp_path):
    import httpx
    import hishel
    from moodle_scraper.session import _MoodleSqliteStorage

    cfg = ScraperConfig(
        base_url="https://aulas.itu.uncu.edu.ar/itu/",
        user="alvaro",
        password="wrong_password",
    )
    session = MoodleSession(cfg)

    get_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls
        if request.method == "GET" and str(request.url) == "https://aulas.itu.uncu.edu.ar/itu/login/index.php":
            get_calls += 1
            return httpx.Response(
                200,
                headers={
                    "Set-Cookie": f"MoodleSession=sess_{get_calls}; path=/itu/; secure; SameSite=None",
                    "Content-Type": "text/html; charset=utf-8",
                },
                content=f'<input type="hidden" name="logintoken" value="token_{get_calls}">'.encode(),
            )
        if request.method == "POST" and str(request.url) == "https://aulas.itu.uncu.edu.ar/itu/login/index.php":
            body = request.content.decode()
            cookie_hdr = request.headers.get("cookie", "")
            expected_token = f"logintoken=token_{get_calls}"
            expected_cookie = f"MoodleSession=sess_{get_calls}"
            if expected_token in body and expected_cookie in cookie_hdr and "password=correct_password" in body:
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    content=b'<html><div class="userbutton">Alvaro ITU</div><script>var M = {cfg: {sesskey: "sk_ok"}};</script></html>',
                )
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b'<html><div id="loginerrormessage">Acceso inv\xc3\xa1lido. Por favor, int\xc3\xa9ntelo otra vez.</div></html>',
            )
        return httpx.Response(404)

    storage = _MoodleSqliteStorage(database_path=str(tmp_path / "cache.sqlite"))
    transport = hishel.httpx.CacheTransport(
        next_transport=httpx.MockTransport(handler),
        storage=storage,
        rfc=True,
    )
    session.client = hishel.httpx.CacheClient(
        transport=transport,
        follow_redirects=True,
    )
    session.session = session.client

    # Primer intento con contraseña incorrecta debe lanzar MoodleAuthError (credenciales inválidas)
    with pytest.raises(MoodleAuthError) as exc_info:
        session.login()
    assert session.is_invalid_credentials_error(exc_info.value) is True
    assert get_calls == 1

    # Segundo intento tras actualizar la contraseña NO debe servir login/index.php desde caché
    cfg.password = "correct_password"
    assert session.login() is True
    assert get_calls == 2
    assert session.is_authenticated is True
    assert session.sesskey == "sk_ok"
    assert session.user_fullname == "Alvaro ITU"
    storage.close()


def test_extract_user_fullname_ignores_usermenu_dropdown():
    cfg = ScraperConfig(base_url="https://aulas.itu.uncu.edu.ar/itu/")
    session = MoodleSession(cfg)
    dashboard_html = """
    <html>
        <div class="usermenu">
            <div class="dropdown-menu">
                <a href="#">Perfil</a><a href="#">Calificaciones</a><a href="#">Cerrar sesión</a>
            </div>
        </div>
        <div class="logininfo">
            Usted se ha identificado como <a href="https://aulas.itu.uncu.edu.ar/itu/user/profile.php?id=999">JUAN PEREZ</a>
        </div>
    </html>
    """
    assert session._extract_user_fullname(dashboard_html) == "JUAN PEREZ"


