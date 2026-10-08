"""Local FastAPI entry point and server-rendered reader route."""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
import tempfile
import traceback
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.cleaner import clean
from app.browser import close_browser_context, complete_xhs_login, open_xhs_login_page
from app.config import DATA_DIR
from app.db import get_document, init_db
from app.exceptions import FetchError
from app.publisher import publish
from app.router import extract_xhs_note_url, route


logger = logging.getLogger(__name__)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
UPLOAD_TMP_DIR = DATA_DIR / "tmp"
MAX_PDF_UPLOAD_BYTES = 20 * 1024 * 1024
_URL_QUERY_RE = re.compile(r"(?i)(https?://[^\s'\"<>?]+)\?[^\s'\"<>]*")


def _safe_traceback(exc: BaseException) -> str:
    """Keep diagnostic stacks useful without writing article query tokens to logs."""
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return _URL_QUERY_RE.sub(r"\1?<redacted>", formatted)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        yield
    finally:
        # U7 may create the shared persistent Edge context while handling a
        # submission. Close it during graceful server shutdown so its profile
        # lock is released before the next foreground run.
        await close_browser_context()


app = FastAPI(title="AI Reader", version="0.1.0", lifespan=lifespan)


def _render_index(
    request: Request,
    *,
    error: str | None = None,
    read_url: str | None = None,
    status_message: str | None = None,
    input_url: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": error,
            "read_url": read_url,
            "status_message": status_message,
            "input_url": input_url,
        },
        status_code=status_code,
    )


def _safe_upload_filename(filename: str) -> str:
    # Browser-supplied file names are display metadata only, never disk paths.
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


async def _save_pdf_upload(upload: UploadFile) -> tuple[Path, str]:
    original_name = _safe_upload_filename(upload.filename or "")
    if not original_name or Path(original_name).suffix.lower() != ".pdf":
        raise FetchError("上传文件必须是 PDF（.pdf）格式。")

    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="upload-", suffix=".pdf", dir=UPLOAD_TMP_DIR)
    temp_path = Path(temp_name)
    total_bytes = 0
    signature = bytearray()

    try:
        with os.fdopen(descriptor, "wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_PDF_UPLOAD_BYTES:
                    raise FetchError("PDF 文件超过 20 MiB 上传上限。")
                if len(signature) < 5:
                    signature.extend(chunk[: 5 - len(signature)])
                destination.write(chunk)

        if bytes(signature) != b"%PDF-":
            raise FetchError("上传内容不是有效的 PDF 文件。")
        return temp_path, original_name
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    """Render the local URL and PDF submission form."""
    return _render_index(request)


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    """Expose the original U0 health response at a dedicated endpoint."""
    return "ai-reader running"


@app.post("/xhs/login", response_class=HTMLResponse)
async def open_xhs_login(request: Request) -> HTMLResponse:
    """Open the XHS login page in the persistent headed Edge context."""
    try:
        await open_xhs_login_page()
    except FetchError as exc:
        return _render_index(request, error=str(exc))
    return _render_index(
        request,
        status_message="已在 Edge 中打开小红书登录页；完成登录后返回此页点击“我已完成登录”。",
    )


@app.post("/xhs/login/complete", response_class=HTMLResponse)
async def finish_xhs_login(request: Request) -> HTMLResponse:
    """Release the fetch gate after the user confirms manual login."""
    await complete_xhs_login()
    return _render_index(request, status_message="已确认登录流程完成，现在可以提交小红书笔记链接。")


@app.post("/", response_class=HTMLResponse)
async def submit(
    request: Request,
    url: str = Form(default=""),
    pdf_file: UploadFile | None = File(default=None),
) -> HTMLResponse:
    """Run the supported local fetch → clean → publish flow."""
    input_url = url.strip()
    has_upload = pdf_file is not None and bool(pdf_file.filename)
    temp_path: Path | None = None
    stage = "validate_input"

    try:
        if bool(input_url) == has_upload:
            raise FetchError("请填写一个公众号或小红书链接，或选择一个 PDF 文件；每次只提交一种输入。")

        if has_upload:
            stage = "save_pdf_upload"
            assert pdf_file is not None
            temp_path, original_name = await _save_pdf_upload(pdf_file)
            stage = "fetch_pdf"
            fetched = await route(temp_path)
            fetched["source_url"] = original_name
            if fetched.get("title") == temp_path.stem:
                fetched["title"] = Path(original_name).stem
        else:
            parsed = urlsplit(input_url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                if extract_xhs_note_url(input_url) is None:
                    raise FetchError(
                        "请提交受支持的公众号链接、小红书笔记链接或包含有效小红书链接的分享文本；PDF 请使用文件上传。"
                    )
            stage = "fetch_source"
            fetched = await route(input_url)

        stage = "clean_content"
        cleaned = await run_in_threadpool(clean, fetched)
        stage = "publish_content"
        slug = await run_in_threadpool(publish, cleaned, fetched)
        return _render_index(request, read_url=f"/read/{slug}", input_url=input_url)
    except FetchError as exc:
        return _render_index(request, error=str(exc), input_url=input_url)
    except Exception as exc:
        logger.error(
            "Submission failed at stage=%s (%s).\n%s",
            stage,
            type(exc).__name__,
            _safe_traceback(exc),
        )
        return _render_index(request, error="处理失败，请检查输入或稍后重试。", input_url=input_url)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if pdf_file is not None:
            await pdf_file.close()


@app.get("/read/{slug}", response_class=PlainTextResponse)
async def read_document(slug: str) -> PlainTextResponse:
    """Return the complete title and Markdown body directly in the response."""
    document = get_document(slug)
    if document is None:
        raise HTTPException(status_code=404, detail="阅读内容不存在。")

    body = f"{document['title']}\n\n{document['content_md']}\n"
    return PlainTextResponse(body)
