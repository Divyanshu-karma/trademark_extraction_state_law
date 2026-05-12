#variation_extraction_compumark.py
import argparse
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import fitz
import orjson

import compumark_validation
import layer1extraction
import state_image


REQUIRED_ENV_NAME = "extraction_state_low"
ALLOWED_GROUPS = {"One", "Two", "Three", "Four", "Five"}
ST_RE = re.compile(r"^ST\s*[-\u2212]\s*(\d+)$")
GROUP_RE = re.compile(r"^Group:\s*(One|Two|Three|Four|Five)$")
GROUP_LABEL_RE = re.compile(r"^Group:\s*$")
STATE_PAGE_RE = re.compile(r"^(?:Page|State Page):\s*(\d+)$")
LABELS = {
    "State:",
    "Status:",
    "Date Registered:",
    "Registration No.:",
    "Renewed:",
    "Renewal",
    "Goods/Services:",
    "State Class:",
    "First Use In State:",
    "First Use Anywhere:",
    "Design Phrase:",
    "Disclaimer:",
    "Registrant:",
    "Renewed To:",
    "Manner Of Display:",
    "Filing Correspondent:",
    "Search:",
    "State Page:",
    "Analyst:",
}
MARK_TEXT_FORBIDDEN_FRAGMENTS = (
    "Search:",
    "State Page:",
    "Analyst:",
    "Goods/Services:",
    "Registrant:",
    "Group:",
    "State:",
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def require_conda_env() -> None:
    env_name = os.environ.get("CONDA_DEFAULT_ENV", "")
    if env_name and env_name != REQUIRED_ENV_NAME:
        raise RuntimeError(
            f"This extractor must run in conda env '{REQUIRED_ENV_NAME}', "
            f"but CONDA_DEFAULT_ENV is '{env_name}'."
        )


def is_bold_font(font_name: str) -> bool:
    return "bold" in font_name.lower() or "black" in font_name.lower()


def color_rgb(color: int) -> tuple[int, int, int]:
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255)


def is_black_color(color: int) -> bool:
    r, g, b = color_rgb(color)
    return r < 70 and g < 70 and b < 70


def page_lines(page: fitz.Page) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = []
            for span in line.get("spans", []):
                text = normalize_text(span.get("text", ""))
                if not text:
                    continue
                spans.append(
                    {
                        "text": text,
                        "bbox": tuple(float(v) for v in span["bbox"]),
                        "font": span.get("font", ""),
                        "size": float(span.get("size", 0.0)),
                        "color": int(span.get("color", 0)),
                    }
                )
            text = normalize_text(" ".join(span["text"] for span in spans))
            if not text:
                continue
            sizes = [span["size"] for span in spans]
            fonts = [span["font"] for span in spans]
            colors = [span["color"] for span in spans]
            lines.append(
                {
                    "text": text,
                    "bbox": tuple(float(v) for v in line["bbox"]),
                    "spans": spans,
                    "max_size": max(sizes) if sizes else 0.0,
                    "is_bold": any(is_bold_font(font) for font in fonts),
                    "is_black": any(is_black_color(color) for color in colors),
                }
            )
    return sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))


def is_label_text(text: str) -> bool:
    if text in LABELS:
        return True
    return any(text.startswith(label + " ") for label in LABELS if label != "Renewal")


def split_inline_label(text: str, label: str) -> str | None:
    if text == label:
        return ""
    if text.startswith(label + " "):
        return normalize_text(text[len(label) :])
    return None


def value_for_adjacent_label(lines: list[dict[str, Any]], index: int, label_re: re.Pattern[str]) -> str:
    line = lines[index]
    label_match = label_re.match(line["text"])
    if not label_match:
        return ""
    if label_match.lastindex:
        return normalize_text(label_match.group(1))

    label_bottom = line["bbox"][3]
    label_x0 = line["bbox"][0]
    for following in lines[index + 1 : index + 5]:
        x0, y0, _x1, _y1 = following["bbox"]
        if y0 < label_bottom - 1:
            continue
        if abs(x0 - label_x0) > 35:
            continue
        return normalize_text(following["text"])
    return ""


def first_value_after_label(lines: list[dict[str, Any]], label: str) -> str:
    for index, line in enumerate(lines):
        inline_value = split_inline_label(line["text"], label)
        if inline_value is None:
            continue
        if inline_value:
            return inline_value
        for following in lines[index + 1 :]:
            if is_label_text(following["text"]):
                return ""
            return normalize_text(following["text"])
    return ""


def collect_block_after_label(lines: list[dict[str, Any]], label: str) -> list[str]:
    for index, line in enumerate(lines):
        inline_value = split_inline_label(line["text"], label)
        if inline_value is None:
            continue
        values = [inline_value] if inline_value else []
        for following in lines[index + 1 :]:
            if is_label_text(following["text"]):
                break
            values.append(normalize_text(following["text"]))
        return [value for value in values if value]
    return []


def extract_goods_services(lines: list[dict[str, Any]]) -> tuple[list[int], str, str]:
    structured_result = compumark_validation.extract_goods_services_structured(lines)
    if structured_result != ([], "", ""):
        return structured_result

    goods_lines = collect_block_after_label(lines, "Goods/Services:")
    intl_class: list[int] = []
    description_parts: list[str] = []
    first_use = ""

    for raw_line in goods_lines:
        line = normalize_text(raw_line)
        if line.startswith("International Class:"):
            intl_class = [
                int(value)
                for value in re.findall(r"\b([0-9]|[1-3]\d|4[0-5])\b", line)
            ]
            continue
        if line.startswith("First Use In State:"):
            first_use = normalize_text(line.split(":", 1)[1])
            continue
        if line.startswith(("State Class:", "First Use Anywhere:")):
            continue
        description_parts.append(line)

    if not first_use:
        first_use = first_value_after_label(lines, "First Use In State:")

    return intl_class, normalize_text(" ".join(description_parts)), first_use


def find_group_below_st(lines: list[dict[str, Any]], st_index: int, st_line: dict[str, Any]) -> str:
    st_x0, st_y0, st_x1, st_y1 = st_line["bbox"]
    for next_index in range(st_index + 1, min(st_index + 7, len(lines))):
        next_line = lines[next_index]
        x0, y0, _x1, _y1 = next_line["bbox"]
        if y0 < st_y1 - 1:
            continue
        if y0 - st_y0 > 90:
            break
        if abs(x0 - st_x0) > max(45.0, (st_x1 - st_x0) + 25.0):
            continue

        group_match = GROUP_RE.match(next_line["text"])
        if group_match:
            return group_match.group(1)

        if GROUP_LABEL_RE.match(next_line["text"]):
            group_value = value_for_adjacent_label(lines, next_index, GROUP_RE)
            if group_value in ALLOWED_GROUPS:
                return group_value

        if next_line["text"] in ALLOWED_GROUPS:
            prev_line = lines[next_index - 1] if next_index > 0 else {}
            if prev_line.get("text") == "Group:":
                return next_line["text"]
    return ""


def find_st_and_group(lines: list[dict[str, Any]], page_width: float) -> tuple[int, str, dict[str, Any]] | None:
    typical_size = median([line["max_size"] for line in lines]) if lines else 0.0
    for index, line in enumerate(lines):
        match = ST_RE.match(line["text"])
        if not match:
            continue
        x0, y0, _x1, _y1 = line["bbox"]
        visually_valid = line["is_bold"] and line["max_size"] >= max(8.5, typical_size - 0.25)
        header_position_valid = y0 < 140 and x0 >= page_width * 0.45
        # Some CompuMark PDFs extract the right-aligned header as a left x coordinate
        # near mid-page. Keep the position check broad but still header-bound.
        if not (visually_valid and header_position_valid):
            continue
        group = find_group_below_st(lines, index, line)
        if group:
            return int(match.group(1)), group, line
    return None


def mostly_numeric_or_symbolic(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    alpha_count = len(re.findall(r"[A-Za-z]", compact))
    return alpha_count == 0 or alpha_count / len(compact) < 0.30


def is_mark_text_candidate(line: dict[str, Any]) -> bool:
    text = normalize_text(line["text"])
    if not text or len(text) > 160:
        return False
    if ST_RE.match(text) or GROUP_RE.match(text) or GROUP_LABEL_RE.match(text):
        return False
    if compumark_validation.is_known_label(text):
        return False
    if any(fragment in text for fragment in MARK_TEXT_FORBIDDEN_FRAGMENTS):
        return False
    if text.startswith(("Search ", "State Page ", "Analyst ")):
        return False
    if mostly_numeric_or_symbolic(text):
        return False
    return bool(line["is_bold"] and line["is_black"])


def extract_mark_text(lines: list[dict[str, Any]], st_line: dict[str, Any]) -> str:
    st_bottom = st_line["bbox"][3]
    state_top = next(
        (line["bbox"][1] for line in lines if line["text"] == "State:" or line["text"].startswith("State: ")),
        None,
    )
    candidates = []
    for line in lines:
        y0 = line["bbox"][1]
        if y0 <= st_bottom or (state_top is not None and y0 >= state_top):
            continue
        if is_mark_text_candidate(line):
            candidates.append(line)
    if not candidates:
        return ""
    candidates.sort(
        key=lambda line: (
            -float(line["max_size"]),
            float(line["bbox"][1]) - st_bottom,
            len(normalize_text(line["text"])),
            float(line["bbox"][0]),
        )
    )
    return normalize_text(candidates[0]["text"])


def extract_page(page: fitz.Page) -> dict[str, Any] | None:
    lines = page_lines(page)
    detected = find_st_and_group(lines, float(page.rect.width))
    if detected is None:
        return None
    st_number, group, st_line = detected
    intl_class, goods_description, first_use = extract_goods_services(lines)

    return {
        "ST": st_number,
        "Group": group,
        "mark_text": extract_mark_text(lines, st_line),
        "State": first_value_after_label(lines, "State:"),
        "Status": first_value_after_label(lines, "Status:"),
        "registration_no": first_value_after_label(lines, "Registration No.:"),
        "owner_name": first_value_after_label(lines, "Registrant:"),
        "intl_class": intl_class,
        "goods_services_description": goods_description,
        "first_use_in_state": first_use,
        "design_phrase": normalize_text(" ".join(collect_block_after_label(lines, "Design Phrase:"))),
        "manner_of_display": normalize_text(" ".join(collect_block_after_label(lines, "Manner Of Display:"))),
        "filing_correspondent": first_value_after_label(lines, "Filing Correspondent:"),
        "state_image_path": "not_exist",
        "Image_Base64": "not_exist",
        "has_image": False,
        "image_status": "no_image_detected",
        "image_retry_attempted": False,
        "image_recovered": False,
    }


def parse_layer1_state_range(pdf_path: str) -> tuple[int, int] | None:
    layer1_data = layer1extraction.extract_pdf(pdf_path)
    try:
        start_page = int(layer1_data.get("state_starting_page", ""))
        end_page = int(layer1_data.get("state_end_page", ""))
    except (TypeError, ValueError):
        return None
    if start_page <= 0 or end_page < start_page:
        return None
    return start_page, end_page


def state_range_page_indices(doc: fitz.Document, start_page: int, end_page: int) -> list[int]:
    page_indices: list[int] = []
    for page_index, page in enumerate(doc):
        lines = layer1extraction.page_lines(page)
        report_page = None
        for line in lines:
            match = STATE_PAGE_RE.match(line["text"])
            if match:
                report_page = int(match.group(1))
        if report_page is None:
            continue
        if start_page <= report_page <= end_page:
            page_indices.append(page_index)
    return page_indices


def image_status_from_result(image_result: dict[str, Any]) -> str:
    explicit_status = image_result.get("image_status")
    if explicit_status:
        return normalize_text(str(explicit_status))

    state_image_path = image_result.get("state_image_path", "not_exist")
    image_base64 = image_result.get("Image_Base64", "not_exist")
    has_image_path = compumark_validation.image_field_exists(state_image_path)
    has_image_base64 = compumark_validation.image_field_exists(image_base64)

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


def extract_pdf(pdf_path: str) -> dict[str, list[dict[str, Any]]]:
    state_range = parse_layer1_state_range(pdf_path)
    if state_range is None:
        return {"state_summary_data": []}

    start_page, end_page = state_range
    with fitz.open(pdf_path) as doc:
        page_indices = state_range_page_indices(doc, start_page, end_page)
        image_results = state_image.upload_state_range_images(
            pdf_path,
            page_indices,
            filename_prefix=f"{Path(pdf_path).stem}_state_{start_page}_{end_page}",
        )

        detected_rows = []
        for page_index in page_indices:
            page = doc[page_index]
            row = extract_page(page)
            if row is None:
                continue
            lines = page_lines(page)
            image_result = image_results.get(page_index, {})
            attach_image_fields(row, image_result)
            validation_result = compumark_validation.validate_extracted_row(row, lines)
            accepted_validation = validation_result
            if not validation_result.is_valid:
                recovered_row, recovered_lines = compumark_validation.recover_extracted_row(row, lines)
                recovered_validation = compumark_validation.validate_extracted_row(
                    recovered_row, recovered_lines
                )
                recovered_validation.recovered = True
                recovered_validation.add_warning("validation: recovered row accepted")
                if not recovered_validation.is_valid:
                    continue
                row = recovered_row
                accepted_validation = recovered_validation
            row["_validation"] = accepted_validation.to_dict()
            detected_rows.append(row)

    rows: list[dict[str, Any]] = []
    expected_st = 1
    for row in detected_rows:
        validation = row.get("_validation", {})
        if not validation.get("is_valid", False):
            continue
        if row["ST"] == expected_st:
            rows.append(row)
            expected_st += 1
        elif row["ST"] == 1 and not rows:
            rows.append(row)
            expected_st = 2

    return {"state_summary_data": rows}


def timestamped_output_path(pdf_path: str) -> str:
    pdf_stem = Path(pdf_path).stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_stem).strip("._-") or "compumark"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_stem}_state_summary_{timestamp}.json"


def timestamped_output_path_in_json_image_folder(pdf_path: str) -> str:
    output_dir = Path("json+image_compumark_var2")
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / timestamped_output_path(pdf_path))


def serialize_json_payload(data: dict[str, Any]) -> str:
    try:
        # orjson returns bytes; decode to preserve existing text file/stdout behavior.
        return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode("utf-8")
    except orjson.JSONEncodeError:
        logging.exception("Failed to serialize extraction output as JSON.")
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract CompuMark state trademark detail pages into strict JSON."
    )
    parser.add_argument(
        "pdf_path",
        nargs="?",
        default="Search Report - CARB RIGHT.pdf",
        help="Input CompuMark PDF path.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Optional JSON output file. If omitted, JSON is printed to stdout.",
    )
    return parser.parse_args()


def main() -> None:
    require_conda_env()
    args = parse_args()
    data = extract_pdf(args.pdf_path)
    payload = serialize_json_payload(data)
    output_path = args.output or timestamped_output_path_in_json_image_folder(args.pdf_path)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
