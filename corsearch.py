#corsearch.py
import argparse
import re
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import fitz
import orjson

import state_image
from layer1extraction import extract_pdf, line_same_row, normalize_text, page_lines


DEFAULT_PDF_PATH = "corsearch/Search report - RAZZ UP!.PDF"
OUTPUT_DIR = "corsearch_result"
STATE_RESULTS_HEADING = "STATESEARCHRESULTS"


def compact_heading(value: str) -> str:
    return re.sub(r"[^A-Za-z]+", "", value).upper()


def is_state_search_results_page(page: fitz.Page) -> bool:
    lines = page_lines(page)
    sizes = [line["max_size"] for line in lines]
    typical_size = median(sizes) if sizes else 0.0
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)

    for line in lines:
        if compact_heading(line["text"]) != STATE_RESULTS_HEADING:
            continue
        x0, y0, x1, _y1 = line["bbox"]
        center_x = (x0 + x1) / 2
        centered = abs(center_x - page_width / 2) <= page_width * 0.18
        in_header = page_height * 0.06 <= y0 <= page_height * 0.18
        visually_dominant = line["is_bold"] and line["max_size"] >= max(14.0, typical_size + 4.0)
        if centered and in_header and visually_dominant:
            return True
    return False


def page_footer(page: fitz.Page) -> int | None:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    footer_rect = fitz.Rect(0, page_height * 0.88, page_width, page_height)
    footer_text = page.get_text("text", clip=footer_rect)
    for line in footer_text.splitlines():
        match = re.fullmatch(r"\s*Page\s+(\d+)\s*", line, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def pages_by_footer_range(doc: fitz.Document, start_page: int, end_page: int) -> dict[int, fitz.Page]:
    pages: dict[int, fitz.Page] = {}
    for page in doc:
        footer = page_footer(page)
        if footer is not None and start_page <= footer <= end_page:
            pages[footer] = page
    return pages


def value_on_label_row(lines: list[dict[str, Any]], label: str) -> str:
    for line in lines:
        text = line["text"]
        if text == label:
            values = [
                candidate
                for candidate in lines
                if candidate is not line
                and candidate["bbox"][0] > line["bbox"][2]
                and line_same_row(line, candidate)
            ]
            if values:
                return normalize_text(" ".join(item["text"] for item in sorted(values, key=lambda item: item["bbox"][0])))
            return ""
        if text.startswith(label):
            return normalize_text(text[len(label) :])
    return ""


def extract_mark_text(lines: list[dict[str, Any]], page: fitz.Page) -> str:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    candidates = [
        line
        for line in lines
        if line["is_bold"]
        and line["bbox"][0] <= page_width * 0.45
        and page_height * 0.08 <= line["bbox"][1] <= page_height * 0.35
        and line["max_size"] >= 11.0
        and not re.fullmatch(r"US-\d+", line["text"])
        and compact_heading(line["text"]) != STATE_RESULTS_HEADING
        and any(char.isalpha() for char in line["text"])
    ]
    if not candidates:
        return ""
    return sorted(candidates, key=lambda item: (-item["max_size"], item["bbox"][1], item["bbox"][0]))[0]["text"]


def extract_intl_classes(value: str) -> list[int]:
    before_us_class = re.split(r"/\s*U\.?S\.?\s*Class", value, maxsplit=1, flags=re.IGNORECASE)[0]
    classes: list[int] = []
    for number_text in re.findall(r"\d+", before_us_class):
        number = int(number_text)
        if 1 <= number <= 45 and number not in classes:
            classes.append(number)
    return classes


def extract_goods_services(lines: list[dict[str, Any]]) -> tuple[list[int], str, str]:
    intl_class: list[int] = []
    first_use = ""
    description_lines: list[str] = []
    in_goods = False
    after_first_use = False

    for line in lines:
        text = line["text"]
        if text == "GOODS/SERVICES":
            in_goods = True
            continue
        if in_goods and text == "OWNER INFORMATION":
            break
        if not in_goods:
            continue
        if text.startswith("Int'l. Class:"):
            intl_class = extract_intl_classes(text)
            continue
        if text.startswith("First Use:"):
            first_use = normalize_text(text[len("First Use:") :])
            after_first_use = True
            continue
        if after_first_use and text:
            description_lines.append(text)

    return intl_class, "\n".join(description_lines), first_use


def extract_owner_name(lines: list[dict[str, Any]]) -> str:
    for index, line in enumerate(lines):
        if line["text"] != "Registrant:":
            continue
        for next_line in lines[index + 1 :]:
            if next_line["bbox"][1] <= line["bbox"][3]:
                continue
            return next_line["text"]
    return ""


def image_field_exists(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value != "not_exist"


def image_status_from_result(image_result: dict[str, Any]) -> str:
    explicit_status = image_result.get("image_status")
    if explicit_status:
        return normalize_text(str(explicit_status))

    state_image_path = image_result.get("state_image_path", "not_exist")
    image_base64 = image_result.get("Image_Base64", "not_exist")
    has_image_path = image_field_exists(state_image_path)
    has_image_base64 = image_field_exists(image_base64)

    if has_image_path and has_image_base64:
        return "success"
    if image_result.get("has_image") and has_image_base64 and not has_image_path:
        return "upload_failed"
    if image_result.get("has_image") and has_image_path and not has_image_base64:
        return "base64_failed"
    if not image_result:
        return "unexpected_error"
    return "no_image_detected"


def attach_image_fields(row: dict[str, Any], image_result: dict[str, Any]) -> None:
    row["state_image_path"] = image_result.get("state_image_path", "not_exist")
    row["Image_Base64"] = image_result.get("Image_Base64", "not_exist")
    row["has_image"] = bool(image_result.get("has_image", False))
    row["image_status"] = image_status_from_result(image_result)
    row["image_retry_attempted"] = bool(image_result.get("image_retry_attempted", False))
    row["image_recovered"] = bool(image_result.get("image_recovered", False))


def extract_state_page(page: fitz.Page) -> dict[str, Any]:
    lines = page_lines(page)
    intl_class, goods_services_description, first_use = extract_goods_services(lines)
    return {
        "mark_text": extract_mark_text(lines, page),
        "State": value_on_label_row(lines, "State:"),
        "Status": value_on_label_row(lines, "Status:"),
        "Date": value_on_label_row(lines, "Date:"),
        "Registered": value_on_label_row(lines, "Registered:"),
        "registration_no": value_on_label_row(lines, "Registration No.:"),
        "owner_name": extract_owner_name(lines),
        "intl_class": intl_class,
        "goods_services_description": goods_services_description,
        "first_use_in_state": first_use,
        "state_image_path": "not_exist",
        "Image_Base64": "not_exist",
        "has_image": False,
        "image_status": "no_image_detected",
        "image_retry_attempted": False,
        "image_recovered": False,
    }


def timestamped_output_path(pdf_path: str) -> Path:
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(pdf_path).stem).strip("._-")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{safe_stem or 'corsearch'}_state_summary_{timestamp}.json"


def serialize_json_payload(data: dict[str, Any]) -> str:
    return orjson.dumps(
        data,
        option=orjson.OPT_INDENT_2,
    ).decode("utf-8")


def extract_corsearch_state_summary(pdf_path: str) -> dict[str, list[dict[str, Any]]]:
    layer1_data = extract_pdf(pdf_path)
    start_page = int(layer1_data["state_starting_page"])
    end_page = int(layer1_data["state_end_page"])

    state_summary_data: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        pages = pages_by_footer_range(doc, start_page, end_page)
        first_page = pages.get(start_page)
        if first_page is None or not is_state_search_results_page(first_page):
            start_page += 1

        for footer_number in range(start_page, end_page + 1):
            page = pages.get(footer_number)
            if page is None:
                continue
            row = extract_state_page(page)
            image_result = state_image.extract_and_upload_page_image_result(
                doc=doc,
                page_index=page.number,
                filename_prefix=f"corsearch_page_{footer_number}",
            )
            attach_image_fields(row, image_result)
            state_summary_data.append(row)

    return {"state_summary_data": state_summary_data}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract CORSEARCH state trademark result pages.")
    parser.add_argument("pdf_path", nargs="?", default=DEFAULT_PDF_PATH, help="Input CORSEARCH PDF path.")
    parser.add_argument("-o", "--output", help="Optional output JSON path. Defaults to corsearch_result timestamped filename.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = extract_corsearch_state_summary(args.pdf_path)
    payload = serialize_json_payload(data)
    output_path = Path(args.output) if args.output else timestamped_output_path(args.pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
