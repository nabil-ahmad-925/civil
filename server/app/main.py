import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .config import check_config, get_settings
from .routers import cross_refs, documents, figures, nodes

_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
    _app_logger.addHandler(_handler)


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_config(get_settings())
    yield


app = FastAPI(title="Civil Eng Standards API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return JSONResponse({"error": f"Invalid request: {problems}"}, status_code=422)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(documents.router)
app.include_router(nodes.router)
app.include_router(cross_refs.router)
app.include_router(figures.router)
