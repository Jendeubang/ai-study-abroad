from contextlib import asynccontextmanager
import asyncio
import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Annotated

from app.config import settings
from app.crud import get_program
from app.database import get_db, initialize_database, make_engine, make_session_factory, migrate_database
from app.models import Program
from app.routers.chat import build_router as build_chat_router
from app.routers.admin import build_router as build_admin_router
from app.routers.programs import router as programs_router
from app.routers.recommend import build_router as build_recommend_router
from app.services.ai_service import AIService
from app.services.data_review_service import active_evidence, active_rounds, review_alerts


logger = logging.getLogger("study_abroad")
# Uvicorn's default access log includes the query string, which may contain private details.
logging.getLogger("uvicorn.access").disabled = True
_ip_salt = secrets.token_bytes(16)
_app_dir = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_app_dir / "templates"))
DbSession = Annotated[Session, Depends(get_db)]


def safe_external_url(value: str | None) -> str | None:
    if value and value.startswith(("https://", "http://")):
        return value
    return None


templates.env.globals["safe_external_url"] = safe_external_url


def anonymous_rate_limit_key(request: Request) -> str:
    address = get_remote_address(request)
    return hashlib.blake2b(address.encode("utf-8"), key=_ip_salt, digest_size=16).hexdigest()


def _error(request: Request, status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": message, "data": None, "request_id": request.state.request_id},
    )


def create_app(
    database_url: str | None = None,
    request_timeout_seconds: float | None = None,
    ai_service: AIService | None = None,
) -> FastAPI:
    timeout = request_timeout_seconds if request_timeout_seconds is not None else settings.request_timeout_seconds
    if timeout <= 0:
        raise ValueError("REQUEST_TIMEOUT_SECONDS 必须大于零")
    active_database_url = database_url or settings.database_url
    engine = make_engine(active_database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        migrate_database(active_database_url)
        initialize_database(engine, settings.seed_path)
        yield
        engine.dispose()

    app = FastAPI(title="AI 留学检索", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(_app_dir / "static")), name="static")
    app.state.session_factory = make_session_factory(engine)
    app.state.ai_service = ai_service or AIService(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
    )
    app.state.admin_token = settings.admin_token
    limiter = Limiter(key_func=anonymous_rate_limit_key, default_limits=["60/minute"])
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        try:
            response = await asyncio.wait_for(call_next(request), timeout=timeout)
        except TimeoutError:
            response = _error(request, 504, "请求处理超时")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith(("/api/admin/", "/admin/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return _error(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [".".join(map(str, issue["loc"])) for issue in exc.errors()]
        return _error(request, 422, "参数无效: " + ", ".join(fields))

    @app.exception_handler(RateLimitExceeded)
    def rate_limit_error(request: Request, exc: RateLimitExceeded):
        return _error(request, 429, "请求过于频繁")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("request_id=%s error_type=%s", request.state.request_id, type(exc).__name__)
        return _error(request, 500, "服务器暂时不可用")

    @app.get("/api/health", tags=["health"])
    def health(request: Request):
        return {"code": 0, "message": "ok", "data": {"status": "ok"}, "request_id": request.state.request_id}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request, db: DbSession):
        has_official = db.scalar(
            select(Program.id).where(Program.data_origin == "official_reviewed").limit(1)
        ) is not None
        return templates.TemplateResponse(
            request, "index.html", {
                "style": "editorial",
                "initial_query": request.query_params.get("q", ""),
                "default_source_type": "official" if has_official else "all",
            }
        )

    @app.get("/preview/{style}", response_class=HTMLResponse, include_in_schema=False)
    def preview(request: Request, style: str):
        if style not in {"research", "studio"}:
            raise HTTPException(status_code=404, detail="页面方案不存在")
        return templates.TemplateResponse(
            request, "index.html", {
                "style": style,
                "initial_query": request.query_params.get("q", ""),
                "default_source_type": "all",
            }
        )

    @app.get("/programs/{program_id}", response_class=HTMLResponse, include_in_schema=False)
    def detail_page(request: Request, program_id: int, db: DbSession):
        program = get_program(db, program_id)
        if program is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        style = request.query_params.get("style", "editorial")
        if style not in {"editorial", "research", "studio"}:
            style = "editorial"
        updated = program.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return templates.TemplateResponse(
            request, "program_detail.html", {
                "program": program,
                "style": style,
                "deadline_expired": program.deadline is not None and program.deadline < date.today(),
                "data_stale": datetime.now(timezone.utc) - updated > timedelta(days=180),
                "evidence": active_evidence(db, program.id),
                "rounds": active_rounds(db, program.id),
                "review_alerts": review_alerts(db, program),
                "today": date.today(),
            }
        )

    @app.get("/admin/reviews", response_class=HTMLResponse, include_in_schema=False)
    def admin_reviews(request: Request):
        return templates.TemplateResponse(request, "admin_reviews.html", {})

    app.include_router(programs_router)
    app.include_router(build_recommend_router(limiter))
    app.include_router(build_chat_router(limiter))
    app.include_router(build_admin_router(limiter))
    return app


app = create_app()
