"""FastAPI JSON API + static SPA for first-run setup (serves on :8989).

Lifecycle: while ``.env`` has no ``LSM_ADMIN_PASSWORD_HASH`` the stack is
*unconfigured* and the one-time wizard endpoints are open. Once ``POST
/api/complete`` writes an admin user + bcrypt hash, the wizard is sealed and
every ``/api`` route (except ``/api/status`` and ``/api/login``) requires HTTP
Basic auth against those admin credentials.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .download import router as download_router
from .env_file import merge_env, read_env
from .models import CertPayload, CompletePayload, ConfigPayload
from .schema import (
    EDITABLE_KEYS,
    SECRET_KEYS,
    cert_env_updates,
    env_path,
    write_cert,
)

_security = HTTPBasic(auto_error=False)


def _is_configured() -> bool:
    return bool(read_env(env_path()).get("LSM_ADMIN_PASSWORD_HASH"))


def require_admin(
    request: Request,
    creds: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    """Enforce admin basic-auth once the stack is configured (no-op before)."""
    env = read_env(env_path())
    hashed = env.get("LSM_ADMIN_PASSWORD_HASH")
    if not hashed:
        return  # pre-setup: wizard is open
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(creds.username, env.get("LSM_ADMIN_USER", "admin"))
    pass_ok = bcrypt.checkpw(creds.password.encode(), hashed.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def require_unconfigured() -> None:
    """Reject one-time-setup writes once the stack is already configured."""
    if _is_configured():
        raise HTTPException(status.HTTP_409_CONFLICT, "already configured")


def create_setup_app() -> FastAPI:
    app = FastAPI(title="llm-session-manager setup", version="0.1.0")

    @app.get("/api/status")
    def get_status() -> dict:
        return {"configured": _is_configured()}

    @app.get("/api/config", dependencies=[Depends(require_admin)])
    def get_config() -> dict:
        env = read_env(env_path())
        # Never leak secret values; report only whether each is set.
        cfg = {k: env.get(k, "") for k in EDITABLE_KEYS if k not in SECRET_KEYS}
        secrets_set = {k: bool(env.get(k)) for k in SECRET_KEYS}
        return {"config": cfg, "secrets_set": secrets_set}

    @app.post("/api/config", dependencies=[Depends(require_admin)])
    def post_config(payload: ConfigPayload) -> dict:
        updates = payload.filtered(EDITABLE_KEYS)
        if not updates:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid keys")
        merge_env(env_path(), updates)
        return {"written": sorted(updates)}

    @app.post("/api/certs", dependencies=[Depends(require_admin)])
    def post_certs(payload: CertPayload) -> dict:
        try:
            crt, key = write_cert(payload.slot, payload.cert_pem, payload.key_pem)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        # For a per-vhost slot, also point nginx at these files in .env so the
        # separate cert is actually used (the shared slot needs no extra env).
        env_updates = cert_env_updates(payload.slot)
        if env_updates:
            merge_env(env_path(), env_updates)
        return {
            "cert": str(crt.name),
            "key": str(key.name),
            "env_written": sorted(env_updates),
        }

    @app.post("/api/complete", dependencies=[Depends(require_unconfigured)])
    def post_complete(payload: CompletePayload) -> dict:
        if len(payload.password) < 8:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "password must be >= 8 characters"
            )
        hashed = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
        merge_env(
            env_path(),
            {"LSM_ADMIN_USER": payload.username, "LSM_ADMIN_PASSWORD_HASH": hashed},
        )
        return {"configured": True}

    app.include_router(download_router, dependencies=[Depends(require_admin)])

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React SPA (web/setup/dist) if present."""
    dist = Path(__file__).resolve().parent / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
    else:

        @app.get("/")
        def _no_ui() -> JSONResponse:
            return JSONResponse(
                {"detail": "SPA not built; run `npm run build` in web/setup."},
                status_code=503,
            )


app = create_setup_app()


def run() -> None:
    """Console entrypoint (``lsm-setup``): serve the setup app on :8989."""
    import uvicorn

    from ..config import settings

    uvicorn.run(
        "lsm.setup.app:app",
        host=settings.setup_host,
        port=settings.setup_port,
        log_level=settings.log_level.lower(),
    )
