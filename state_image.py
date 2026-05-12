#state_image.py
import base64
import email.utils
import hashlib
import hmac
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import fitz


DEFAULT_CONTAINER_NAME = "state-images"
ENV_PATH = Path(__file__).resolve().parent / ".env"
IMAGE_STATUS_SUCCESS = "success"
IMAGE_STATUS_NO_IMAGE = "no_image_detected"
IMAGE_STATUS_RENDER_FAILED = "render_failed"
IMAGE_STATUS_UPLOAD_FAILED = "upload_failed"
IMAGE_STATUS_BASE64_FAILED = "base64_failed"
IMAGE_STATUS_UNEXPECTED_ERROR = "unexpected_error"
RETRYABLE_IMAGE_STATUSES = {
    IMAGE_STATUS_RENDER_FAILED,
    IMAGE_STATUS_UPLOAD_FAILED,
    IMAGE_STATUS_BASE64_FAILED,
    IMAGE_STATUS_UNEXPECTED_ERROR,
}


class ImageRenderError(RuntimeError):
    pass


class ImageBase64Error(RuntimeError):
    pass


def load_env_file(env_path: Path = ENV_PATH) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_connection_string(connection_string: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in connection_string.split(";"):
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key] = value
    required = {"AccountName", "AccountKey"}
    missing = required - values.keys()
    if missing:
        raise RuntimeError(f"Azure connection string missing: {', '.join(sorted(missing))}")
    return values


def _safe_blob_part(value: str) -> str:
    value = value or "state"
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value or "state"


def _canonicalized_headers(headers: dict[str, str]) -> str:
    x_ms_headers = {
        key.lower(): " ".join(value.strip().split())
        for key, value in headers.items()
        if key.lower().startswith("x-ms-")
    }
    return "".join(f"{key}:{x_ms_headers[key]}\n" for key in sorted(x_ms_headers))


def _canonicalized_resource(account_name: str, path: str, query: dict[str, str] | None = None) -> str:
    resource = f"/{account_name}{path}"
    if query:
        for key in sorted(query):
            resource += f"\n{key.lower()}:{query[key]}"
    return resource


def _authorization_header(
    account_name: str,
    account_key: str,
    method: str,
    path: str,
    headers: dict[str, str],
    query: dict[str, str] | None = None,
) -> str:
    content_length = headers.get("Content-Length", "")
    if content_length == "0":
        content_length = ""
    string_to_sign = "\n".join(
        [
            method,
            headers.get("Content-Encoding", ""),
            headers.get("Content-Language", ""),
            content_length,
            headers.get("Content-MD5", ""),
            headers.get("Content-Type", ""),
            "",
            headers.get("If-Modified-Since", ""),
            headers.get("If-Match", ""),
            headers.get("If-None-Match", ""),
            headers.get("If-Unmodified-Since", ""),
            headers.get("Range", ""),
            _canonicalized_headers(headers) + _canonicalized_resource(account_name, path, query),
        ]
    )
    decoded_key = base64.b64decode(account_key)
    signature = base64.b64encode(
        hmac.new(decoded_key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    return f"SharedKey {account_name}:{signature}"


def _azure_request(
    method: str,
    account: dict[str, str],
    path: str,
    body: bytes = b"",
    content_type: str = "",
    query: dict[str, str] | None = None,
) -> urllib.response.addinfourl:
    account_name = account["AccountName"]
    endpoint_suffix = account.get("EndpointSuffix", "core.windows.net")
    scheme = account.get("DefaultEndpointsProtocol", "https")
    url = f"{scheme}://{account_name}.blob.{endpoint_suffix}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)

    has_body = bool(body)
    headers = {
        "x-ms-date": email.utils.formatdate(usegmt=True),
        "x-ms-version": "2023-11-03",
    }
    if has_body:
        headers["Content-Length"] = str(len(body))
    if content_type:
        headers["Content-Type"] = content_type
    headers["Authorization"] = _authorization_header(
        account_name, account["AccountKey"], method, path, headers, query
    )
    request_body = body if method not in {"GET", "HEAD"} and has_body else None
    request = urllib.request.Request(url, data=request_body, headers=headers, method=method)
    return urllib.request.urlopen(request, timeout=60)


def ensure_container(account: dict[str, str], container_name: str) -> None:
    path = f"/{container_name}"
    try:
        _azure_request("PUT", account, path, query={"restype": "container"}).close()
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise


def upload_blob_bytes(image_bytes: bytes, blob_name: str, content_type: str) -> str:
    load_env_file()
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    if not connection_string:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not set in environment or .env.")

    account = parse_connection_string(connection_string)
    container_name = _safe_blob_part(os.environ.get("AZURE_BLOB_CONTAINER", DEFAULT_CONTAINER_NAME)).lower()
    ensure_container(account, container_name)

    encoded_blob = "/".join(urllib.parse.quote(part) for part in blob_name.split("/"))
    path = f"/{container_name}/{encoded_blob}"
    headers_type = content_type or mimetypes.guess_type(blob_name)[0] or "application/octet-stream"

    account_name = account["AccountName"]
    endpoint_suffix = account.get("EndpointSuffix", "core.windows.net")
    scheme = account.get("DefaultEndpointsProtocol", "https")
    headers = {
        "x-ms-date": email.utils.formatdate(usegmt=True),
        "x-ms-version": "2023-11-03",
        "x-ms-blob-type": "BlockBlob",
        "Content-Length": str(len(image_bytes)),
        "Content-Type": headers_type,
    }
    headers["Authorization"] = _authorization_header(account_name, account["AccountKey"], "PUT", path, headers)
    url = f"{scheme}://{account_name}.blob.{endpoint_suffix}{path}"
    request = urllib.request.Request(url, data=image_bytes, headers=headers, method="PUT")
    with urllib.request.urlopen(request, timeout=120):
        pass
    return url


def open_fitz_doc(pdf_path: str) -> fitz.Document:
    return fitz.open(pdf_path)


def extract_page_image(doc: fitz.Document, page_index: int) -> tuple[bytes, str] | None:
    try:
        page = doc[page_index]
        images = page.get_images(full=True)
    except Exception as exc:
        raise ImageRenderError("failed to inspect page images") from exc

    if not images:
        return extract_page_image_block(page)

    best_image: dict[str, Any] | None = None
    best_area = 0
    seen_xrefs: set[int] = set()
    extraction_failed = False
    for image in images:
        try:
            xref = int(image[0])
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            image_info = doc.extract_image(xref)

            image_bytes = image_info.get("image")
            width = int(image_info.get("width", 0) or 0)
            height = int(image_info.get("height", 0) or 0)
        except Exception:
            extraction_failed = True
            continue

        area = width * height
        if not image_bytes or area < 400:
            continue
        if area > best_area:
            best_area = area
            best_image = image_info

    if not best_image:
        if extraction_failed:
            raise ImageRenderError("failed to extract embedded page image")
        return None

    image_bytes = best_image.get("image")
    ext = best_image.get("ext") or "png"
    if not image_bytes:
        return extract_page_image_block(page)
    return image_bytes, ext


def extract_page_image_block(page: fitz.Page) -> tuple[bytes, str] | None:
    try:
        blocks = page.get_text("dict")["blocks"]
    except Exception as exc:
        raise ImageRenderError("failed to inspect page image blocks") from exc

    best_block: dict[str, Any] | None = None
    best_area = 0
    for block in blocks:
        if block.get("type") != 1:
            continue

        image_bytes = block.get("image")
        width = int(block.get("width", 0) or 0)
        height = int(block.get("height", 0) or 0)
        area = width * height
        if not image_bytes or area < 400:
            continue
        if area > best_area:
            best_area = area
            best_block = block

    if not best_block:
        return None

    image_bytes = best_block.get("image")
    ext = best_block.get("ext") or "png"
    if not image_bytes:
        return None
    return image_bytes, ext

def extract_page_image_by_page_number(
    doc: fitz.Document,
    page_number: int,
) -> tuple[bytes, str] | None:
    return extract_page_image(doc, page_number - 1)
def save_image_bytes(image_bytes: bytes, filename_prefix: str, ext: str = "png") -> str:
    safe_prefix = _safe_blob_part(filename_prefix)
    safe_ext = ext.lstrip(".").lower() or "png"
    blob_name = f"state-pages/{safe_prefix}_{uuid.uuid4().hex}.{safe_ext}"
    return upload_blob_bytes(image_bytes, blob_name, f"image/{safe_ext}")


def image_result(
    page_index: int,
    state_image_path: str = "not_exist",
    image_base64: str = "not_exist",
    has_image: bool = False,
    image_status: str = IMAGE_STATUS_NO_IMAGE,
    image_retry_attempted: bool = False,
    image_recovered: bool = False,
) -> dict[str, Any]:
    return {
        "page_index": page_index,
        "state_image_path": state_image_path,
        "Image_Base64": image_base64,
        "has_image": has_image,
        "image_status": image_status,
        "image_retry_attempted": image_retry_attempted,
        "image_recovered": image_recovered,
    }


def encode_image_base64(image_bytes: bytes) -> str:
    try:
        return base64.b64encode(image_bytes).decode("utf-8")
    except Exception as exc:
        raise ImageBase64Error("failed to convert image bytes to base64") from exc


def extract_and_upload_page_image_result_once(
    doc: fitz.Document,
    page_index: int,
    filename_prefix: str,
) -> dict[str, Any]:
    try:
        extracted = extract_page_image(doc, page_index)
        if extracted is None:
            return image_result(page_index)
    except ImageRenderError:
        return image_result(page_index, image_status=IMAGE_STATUS_RENDER_FAILED)
    except Exception:
        return image_result(page_index, image_status=IMAGE_STATUS_UNEXPECTED_ERROR)

    image_bytes, ext = extracted
    try:
        image_base64 = encode_image_base64(image_bytes)
    except ImageBase64Error:
        return image_result(page_index, has_image=True, image_status=IMAGE_STATUS_BASE64_FAILED)

    try:
        uploaded_url = save_image_bytes(image_bytes, filename_prefix, ext)
    except Exception:
        return image_result(
            page_index=page_index,
            image_base64=image_base64,
            has_image=True,
            image_status=IMAGE_STATUS_UPLOAD_FAILED,
        )

    return image_result(
        page_index=page_index,
        state_image_path=uploaded_url,
        image_base64=image_base64,
        has_image=True,
        image_status=IMAGE_STATUS_SUCCESS,
    )


def extract_and_upload_page_image_result(
    doc: fitz.Document,
    page_index: int,
    filename_prefix: str,
) -> dict[str, Any]:
    result = extract_and_upload_page_image_result_once(doc, page_index, filename_prefix)
    if result["image_status"] not in RETRYABLE_IMAGE_STATUSES:
        return result
    retry_result = extract_and_upload_page_image_result_once(doc, page_index, filename_prefix)
    retry_result["image_retry_attempted"] = True
    retry_result["image_recovered"] = retry_result["image_status"] == IMAGE_STATUS_SUCCESS
    return retry_result


def extract_and_upload_page_image(pdf_path: str, page_index: int, filename_prefix: str) -> str:
    with open_fitz_doc(pdf_path) as doc:
        result = extract_and_upload_page_image_result(doc, page_index, filename_prefix)
    return result["state_image_path"]


def upload_state_range_images(
    pdf_path: str,
    page_indices: list[int],
    filename_prefix: str | None = None,
) -> dict[int, dict[str, Any]]:
    prefix = filename_prefix or Path(pdf_path).stem
    results: dict[int, dict[str, Any]] = {}
    with open_fitz_doc(pdf_path) as doc:
        for page_index in page_indices:
            if not 0 <= page_index < doc.page_count:
                continue
            page_prefix = f"{prefix}_pdfpage_{page_index + 1}"
            results[page_index] = extract_and_upload_page_image_result(doc, page_index, page_prefix)
    return results


def load_image_base64(path_or_url: str) -> str | None:
    if not path_or_url:
        return None
    try:
        if path_or_url.startswith(("http://", "https://")):
            with urllib.request.urlopen(path_or_url, timeout=60) as response:
                return base64.b64encode(response.read()).decode("utf-8")
        data = Path(path_or_url).read_bytes()
        return base64.b64encode(data).decode("utf-8")
    except Exception:
        return None
