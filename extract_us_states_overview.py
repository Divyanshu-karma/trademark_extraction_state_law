#for clarivate pdf pattern 
import argparse
import json
import os
import re
from pathlib import Path
from statistics import median
from typing import Any

import fitz

import layer1extraction
import state_image


TARGET_HEADING = "US States Overview List"
SECTION_HEADINGS = [
    "Identical Trademarks",
    "Similar Trademarks",
    "Trademarks in Other Classes",
    "Abandoned Trademarks",
    "Expired Trademarks",
    "Cancelled Trademarks",
]
EXPECTED_COLUMNS = ["Nr.", "Trademark", "Source", "Class(es)", "Status", "Owner", "Number", "Page"]
DETAIL_FIELD_LABELS = {
    "State:",
    "Status:",
    "Mark Type:",
    "Date Registered:",
    "Registration No.:",
    "Renewed:",
    "Expiration Date:",
    "Cancellation Date:",
    "Goods/Services:",
    "State Class:",
    "First Use In State:",
    "First Use Anywhere:",
    "Design Phrase:",
    "Disclaimer:",
    "Registrant:",
    "Renewed To:",
    "Filing Correspondent:",
}
REQUIRED_ENV_NAME = "extraction_state_low"
DEFAULT_OUTPUT_PREFIX = "result_state_low"
PAGE_Y_STRIDE = 1000.0
CANONICAL_COLUMN_X = {
    "Nr.": 58.37,
    "Trademark": 85.31,
    "Source": 208.04,
    "Class(es)": 246.95,
    "Status": 297.84,
    "Owner": 372.68,
    "Number": 471.46,
    "Page": 534.32,
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def require_conda_env() -> None:
    env_name = os.environ.get("CONDA_DEFAULT_ENV", "")
    if env_name and env_name != REQUIRED_ENV_NAME:
        raise RuntimeError(
            f"This extractor must run in conda env '{REQUIRED_ENV_NAME}', "
            f"but CONDA_DEFAULT_ENV is '{env_name}'."
        )


def iter_lines(page: fitz.Page) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = normalize_text(" ".join(span.get("text", "") for span in spans))
            if not text:
                continue
            sizes = [float(span.get("size", 0)) for span in spans]
            fonts = [span.get("font", "") for span in spans]
            lines.append(
                {
                    "text": text,
                    "bbox": tuple(line["bbox"]),
                    "max_size": max(sizes) if sizes else 0.0,
                    "fonts": fonts,
                    "is_bold": any("bold" in font.lower() for font in fonts),
                }
            )
    return lines


def iter_detail_lines(page: fitz.Page) -> list[dict[str, Any]]:
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
                        "bbox": tuple(span["bbox"]),
                        "is_bold": "bold" in span.get("font", "").lower(),
                    }
                )
            text = normalize_text(" ".join(span["text"] for span in spans))
            if text:
                lines.append({"text": text, "bbox": tuple(line["bbox"]), "spans": spans})
    return sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))


def is_detail_label(line: dict[str, Any]) -> bool:
    if line["text"] in DETAIL_FIELD_LABELS:
        return True
    if any(line["text"].startswith(label + " ") for label in DETAIL_FIELD_LABELS):
        return True
    if line["text"].startswith("International Class:"):
        return False
    has_bold = any(span["is_bold"] for span in line["spans"])
    return has_bold and line["text"].endswith(":") and line["bbox"][0] < 180


def detail_value_after_label(lines: list[dict[str, Any]], label: str) -> str:
    for index, line in enumerate(lines):
        if line["text"] != label:
            continue

        values: list[str] = []
        label_right = line["bbox"][2]
        same_line_values = [
            span["text"]
            for span in line["spans"]
            if span["bbox"][0] > label_right + 5 and not span["is_bold"]
        ]
        values.extend(same_line_values)

        for following in lines[index + 1 :]:
            if is_detail_label(following):
                break
            values.append(following["text"])
            break

        return normalize_text(" ".join(values))

    return ""


def extract_goods_services(lines: list[dict[str, Any]]) -> str:
    collecting = False
    parts: list[str] = []

    for line in lines:
        if line["text"] == "Goods/Services:":
            collecting = True
            continue
        if not collecting:
            continue
        if is_detail_label(line):
            break

        non_bold_parts = [span["text"] for span in line["spans"] if not span["is_bold"]]
        if non_bold_parts:
            parts.append(" ".join(non_bold_parts))

    return normalize_text(" ".join(parts).replace("manipu- lation", "manipulation"))


def serial_number_value(row: dict[str, str]) -> str:
    match = re.search(r"\d+", row.get("serialnum", ""))
    return match.group(0) if match else ""


def page_has_st_label(page: fitz.Page, st_label: str) -> bool:
    page_width = float(page.rect.width)
    for line in iter_lines(page):
        if line["text"] != st_label:
            continue
        x0, y0, _x1, _y1 = line["bbox"]
        if line["is_bold"] and x0 > page_width * 0.65 and y0 < 120:
            return True
    return False


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
        report_page = layer1extraction.clarivate_footer_page_number(
            lines,
            float(page.rect.width),
            float(page.rect.height),
        )
        if report_page is None:
            report_page = layer1extraction.footer_page_number(
                lines,
                float(page.rect.width),
                float(page.rect.height),
            )
        if report_page is not None and start_page <= report_page <= end_page:
            page_indices.append(page_index)
    return page_indices


def find_detail_page_by_serial(
    doc: fitz.Document,
    row: dict[str, str],
    page_indices: list[int] | None = None,
) -> int | None:
    number = serial_number_value(row)
    if not number:
        return None

    st_label = f"ST-{number}"
    search_indices = page_indices if page_indices is not None else list(range(doc.page_count))
    for page_index in search_indices:
        if page_has_st_label(doc[page_index], st_label):
            return page_index

    return None


def enrich_row_from_detail_page(
    doc: fitz.Document,
    row: dict[str, str],
    page_indices: list[int] | None = None,
    image_prefix: str | None = None,
) -> None:
    row["state_image_path"] = "not_exist"
    row["Image_Base64"] = "not_exist"
    page_index = find_detail_page_by_serial(doc, row, page_indices)
    if page_index is None:
        row["state"] = ""
        row["Goods/Services"] = ""
        return

    lines = iter_detail_lines(doc[page_index])
    row["state"] = detail_value_after_label(lines, "State:")
    row["Goods/Services"] = extract_goods_services(lines)
    if image_prefix is not None:
        image_result = state_image.extract_and_upload_page_image_result(
            doc,
            page_index,
            f"{image_prefix}_pdfpage_{page_index + 1}",
        )
        row["state_image_path"] = image_result.get("state_image_path", "not_exist")
        row["Image_Base64"] = image_result.get("Image_Base64", "not_exist")


def locate_target_heading(
    doc: fitz.Document,
    page_indices: list[int] | None = None,
) -> tuple[int, dict[str, Any]]:
    all_sizes: list[float] = []
    page_lines: list[tuple[int, list[dict[str, Any]]]] = []
    search_indices = page_indices if page_indices is not None else list(range(doc.page_count))

    for page_index in search_indices:
        lines = iter_lines(doc[page_index])
        page_lines.append((page_index, lines))
        all_sizes.extend(line["max_size"] for line in lines)

    typical_size = median(all_sizes) if all_sizes else 0
    candidates: list[tuple[int, dict[str, Any]]] = []

    for page_index, lines in page_lines:
        for line in lines:
            if normalize_text(line["text"]) != TARGET_HEADING:
                continue
            visually_primary = line["max_size"] >= max(14.0, typical_size + 3.0)
            if visually_primary and line["is_bold"]:
                candidates.append((page_index, line))

    if not candidates:
        raise RuntimeError(f'Could not locate a visually primary "{TARGET_HEADING}" heading.')

    return candidates[0]


def get_words(page: fitz.Page) -> list[dict[str, Any]]:
    words = []
    for item in page.get_text("words"):
        x0, y0, x1, y1, text, block_no, line_no, word_no = item
        words.append(
            {
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "text": text,
                "block_no": block_no,
                "line_no": line_no,
                "word_no": word_no,
            }
        )
    return words


def same_row(a: float, b: float, tolerance: float = 2.0) -> bool:
    return abs(a - b) <= tolerance


def find_first_table_header(
    words: list[dict[str, Any]], min_y: float, max_y: float = float("inf")
) -> tuple[float, dict[str, float]]:
    candidate_ys = sorted(
        {round(word["y0"], 1) for word in words if min_y < word["y0"] < max_y}
    )

    for y in candidate_ys:
        row_words = [word for word in words if same_row(word["y0"], y)]
        by_text = {word["text"]: word for word in row_words}
        if all(column in by_text for column in EXPECTED_COLUMNS):
            return y, {column: by_text[column]["x0"] for column in EXPECTED_COLUMNS}

    raise RuntimeError("Could not locate the table header immediately below the target heading.")


def find_table_header_or_fallback(
    words: list[dict[str, Any]], min_y: float, max_y: float
) -> tuple[float, dict[str, float]]:
    try:
        return find_first_table_header(words, min_y, max_y)
    except RuntimeError:
        header_like = [
            word
            for word in words
            if min_y < word["y0"] < max_y
            and "".join(EXPECTED_COLUMNS).replace(".", "") in word["text"].replace(".", "")
        ]
        if header_like:
            return header_like[0]["y0"], CANONICAL_COLUMN_X.copy()
        return min_y, CANONICAL_COLUMN_X.copy()


def find_section_headings(lines: list[dict[str, Any]], min_y: float) -> list[dict[str, Any]]:
    headings = [
        line
        for line in lines
        if line["bbox"][1] > min_y
        and line["text"] in SECTION_HEADINGS
        and line["is_bold"]
        and line["max_size"] >= 11.5
    ]
    return sorted(headings, key=lambda line: line["bbox"][1])


def find_section_headings_across_pages(
    doc: fitz.Document,
    start_page: int,
    end_page: int,
    first_min_y: float,
) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []

    for page_index in range(start_page, end_page + 1):
        min_y = first_min_y if page_index == start_page else 0.0
        for line in find_section_headings(iter_lines(doc[page_index]), min_y):
            enriched = dict(line)
            enriched["page_index"] = page_index
            enriched["global_y"] = page_index * PAGE_Y_STRIDE + line["bbox"][1]
            headings.append(enriched)

    order = {heading: index for index, heading in enumerate(SECTION_HEADINGS)}
    return sorted(headings, key=lambda line: (line["global_y"], order.get(line["text"], 99)))


def find_table_end_y(lines: list[dict[str, Any]], header_y: float) -> float:
    section_lines = [
        line
        for line in lines
        if line["bbox"][1] > header_y + 8
        and line["is_bold"]
        and line["max_size"] >= 11.5
        and not any(line["text"] == column for column in EXPECTED_COLUMNS)
    ]
    if section_lines:
        return min(line["bbox"][1] for line in section_lines)
    return float("inf")


def next_section_y(section_headings: list[dict[str, Any]], current_heading: dict[str, Any]) -> float:
    current_y = current_heading["bbox"][1]
    following = [line["bbox"][1] for line in section_headings if line["bbox"][1] > current_y]
    return min(following) if following else float("inf")


def next_section_global_y(
    section_headings: list[dict[str, Any]], current_heading: dict[str, Any], default_end: float
) -> float:
    current_y = current_heading["global_y"]
    following = [line["global_y"] for line in section_headings if line["global_y"] > current_y]
    return min(following) if following else default_end


def column_for_word(word: dict[str, Any], column_x: dict[str, float]) -> str | None:
    ordered = sorted(column_x.items(), key=lambda item: item[1])
    adjusted_x0 = word["x0"] + 2.0
    current_column = ordered[0][0]

    for column, x0 in ordered:
        if adjusted_x0 >= x0:
            current_column = column
        else:
            break

    return current_column


def extract_rows_in_bounds(
    page: fitz.Page,
    section_heading: dict[str, Any],
    table_end_y: float,
    include_section_heading: bool = True,
) -> list[dict[str, str]]:
    lines = iter_lines(page)
    words = get_words(page)
    heading_bottom = float(section_heading["bbox"][3])
    header_y, column_x = find_table_header_or_fallback(words, heading_bottom, table_end_y)

    table_words = [
        word
        for word in words
        if word["y0"] > header_y + 4 and word["y0"] < table_end_y - 1
    ]
    table_words.sort(key=lambda word: (word["y0"], word["x0"]))

    row_starts = [
        word
        for word in table_words
        if re.fullmatch(r"\d+\.", word["text"]) and column_for_word(word, column_x) == "Nr."
    ]
    row_starts.sort(key=lambda word: word["y0"])

    extracted: list[dict[str, str]] = []
    for index, start in enumerate(row_starts):
        row_y0 = start["y0"] - 1
        row_y1 = row_starts[index + 1]["y0"] - 1 if index + 1 < len(row_starts) else table_end_y
        row_words = [word for word in table_words if row_y0 <= word["y0"] < row_y1]

        cells = {column: [] for column in EXPECTED_COLUMNS}
        for word in row_words:
            column = column_for_word(word, column_x)
            if column:
                cells[column].append(word)

        def cell_text(column: str) -> str:
            parts = sorted(cells[column], key=lambda word: (word["y0"], word["x0"]))
            return normalize_text(" ".join(word["text"] for word in parts))

        row = {
            "serialnum": cell_text("Nr."),
            "mark_text": cell_text("Trademark"),
            "Source": cell_text("Source"),
            "owner_name": cell_text("Owner"),
            "status": cell_text("Status"),
            "intl_class": cell_text("Class(es)"),
            "Number": cell_text("Number"),
            "Page": cell_text("Page"),
        }
        if include_section_heading:
            row["section_heading"] = section_heading["text"]
        extracted.append(row)

    return extracted


def extract_rows_from_page_region(
    page: fitz.Page,
    section_heading: str,
    min_y: float,
    max_y: float,
) -> list[dict[str, str]]:
    words = get_words(page)
    header_y, column_x = find_table_header_or_fallback(words, min_y, max_y)
    start_y = max(min_y, header_y + 4)

    table_words = [
        word
        for word in words
        if start_y < word["y0"] < max_y - 1
        and word["text"] != "No"
        and not word["text"].startswith("Nr.TrademarkSource")
    ]
    table_words.sort(key=lambda word: (word["y0"], word["x0"]))

    row_starts = [
        word
        for word in table_words
        if re.fullmatch(r"\d+\.", word["text"]) and column_for_word(word, column_x) == "Nr."
    ]
    row_starts.sort(key=lambda word: word["y0"])

    extracted: list[dict[str, str]] = []
    for index, start in enumerate(row_starts):
        row_y0 = start["y0"] - 1
        row_y1 = row_starts[index + 1]["y0"] - 1 if index + 1 < len(row_starts) else max_y
        row_words = [word for word in table_words if row_y0 <= word["y0"] < row_y1]

        cells = {column: [] for column in EXPECTED_COLUMNS}
        for word in row_words:
            column = column_for_word(word, column_x)
            if column:
                cells[column].append(word)

        def cell_text(column: str) -> str:
            parts = sorted(cells[column], key=lambda word: (word["y0"], word["x0"]))
            return normalize_text(" ".join(word["text"] for word in parts))

        row = {
            "serialnum": cell_text("Nr."),
            "mark_text": cell_text("Trademark"),
            "Source": cell_text("Source"),
            "owner_name": cell_text("Owner"),
            "status": cell_text("Status"),
            "intl_class": cell_text("Class(es)"),
            "Number": cell_text("Number"),
            "Page": cell_text("Page"),
            "section_heading": section_heading,
        }
        if row["serialnum"] and row["mark_text"]:
            extracted.append(row)

    return extracted


def extract_rows_across_pages(
    doc: fitz.Document,
    section_heading: dict[str, Any],
    end_global_y: float,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    start_global_y = section_heading["page_index"] * PAGE_Y_STRIDE + section_heading["bbox"][3]
    start_page = int(start_global_y // PAGE_Y_STRIDE)
    end_page = min(int(end_global_y // PAGE_Y_STRIDE), doc.page_count - 1)

    for page_index in range(start_page, end_page + 1):
        page_top_global = page_index * PAGE_Y_STRIDE
        min_y = max(0.0, start_global_y - page_top_global)
        max_y = min(float(doc[page_index].rect.height), end_global_y - page_top_global)
        if max_y <= min_y:
            continue
        rows.extend(
            extract_rows_from_page_region(
                doc[page_index],
                section_heading["text"],
                min_y,
                max_y,
            )
        )

    return rows


def extract_rows(page: fitz.Page, heading: dict[str, Any]) -> list[dict[str, str]]:
    lines = iter_lines(page)
    words = get_words(page)
    heading_bottom = float(heading["bbox"][3])
    header_y, _column_x = find_first_table_header(words, heading_bottom)
    table_end_y = find_table_end_y(lines, header_y)
    return extract_rows_in_bounds(page, heading, table_end_y, include_section_heading=False)


def extract_state_summary(pdf_path: str) -> dict[str, list[dict[str, str]]]:
    state_range = parse_layer1_state_range(pdf_path)
    with fitz.open(pdf_path) as doc:
        range_page_indices = (
            state_range_page_indices(doc, state_range[0], state_range[1])
            if state_range is not None
            else None
        )
        page_index, heading = locate_target_heading(doc, range_page_indices)
        if range_page_indices:
            end_page = max(range_page_indices)
        else:
            toc_pages = [
                max(0, item[2] - 1)
                for item in doc.get_toc()
                if max(0, item[2] - 1) > page_index
            ]
            end_page = min(toc_pages) - 1 if toc_pages else min(doc.page_count - 1, page_index + 4)
        section_headings = find_section_headings_across_pages(
            doc,
            page_index,
            end_page,
            float(heading["bbox"][3]),
        )

        rows: list[dict[str, str]] = []
        for section_heading in section_headings:
            rows.extend(
                extract_rows_across_pages(
                    doc,
                    section_heading,
                    next_section_global_y(
                        section_headings,
                        section_heading,
                        (end_page + 1) * PAGE_Y_STRIDE,
                    ),
                )
            )

        image_prefix = None
        if state_range is not None:
            image_prefix = f"{Path(pdf_path).stem}_state_{state_range[0]}_{state_range[1]}"
        for row in rows:
            enrich_row_from_detail_page(doc, row, range_page_indices, image_prefix)

    return {"state_summary_data": rows}


def next_numbered_output(prefix: str = DEFAULT_OUTPUT_PREFIX) -> str:
    index = 1
    while True:
        path = f"{prefix}_{index}.json"
        if not os.path.exists(path):
            return path
        index += 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract the table immediately following "US States Overview List".'
    )
    parser.add_argument("pdf", nargs="?", default="Turbo Search.pdf")
    parser.add_argument(
        "--output",
        help=(
            "Path to write the JSON payload. If omitted, a numbered file like "
            "result_state_low_1.json, result_state_low_2.json, etc. is created."
        ),
    )
    args = parser.parse_args()

    require_conda_env()
    payload = extract_state_summary(args.pdf)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    output_path = args.output or next_numbered_output()

    print(f"Total data rows: {len(payload['state_summary_data'])}")
    print(f"Output file: {output_path}")
    print(rendered)

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(rendered + "\n")


if __name__ == "__main__":
    main()
