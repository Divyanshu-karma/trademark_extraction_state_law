import json
import os
import re
from statistics import median
from typing import Any

import fitz

import layer1extraction


PDF_PATH = "THE MAGNUM ICE CREAM COMPANY (1).PDF"
TARGET_HEADING_TEXT = "S T A T E  S U M M A R Y"
REQUIRED_ENV_NAME = "extraction_state_low"
PAGE_STRIDE = 1000.0


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def require_conda_env() -> None:
    env_name = os.environ.get("CONDA_DEFAULT_ENV", "")
    if env_name and env_name != REQUIRED_ENV_NAME:
        raise RuntimeError(
            f"This extractor must run in conda env '{REQUIRED_ENV_NAME}', "
            f"but CONDA_DEFAULT_ENV is '{env_name}'."
        )


def page_lines(page: fitz.Page) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = normalize(" ".join(span.get("text", "") for span in spans))
            if not text:
                continue
            sizes = [float(span.get("size", 0)) for span in spans]
            fonts = [span.get("font", "") for span in spans]
            lines.append(
                {
                    "text": text,
                    "bbox": tuple(line["bbox"]),
                    "max_size": max(sizes) if sizes else 0.0,
                    "is_bold": any("bold" in font.lower() for font in fonts),
                }
            )
    return lines


def report_page_number(page: fitz.Page) -> int | None:
    for line in layer1extraction.page_lines(page):
        match = re.match(r"^(?:Page|State Summary Page|State Page):\s*(\d+)$", line["text"])
        if match:
            return int(match.group(1))
    return None


def layer1_state_page_indices(doc: fitz.Document, pdf_path: str) -> list[int]:
    metadata = layer1extraction.extract_pdf(pdf_path)
    try:
        start_page = int(metadata.get("state_starting_page", ""))
        end_page = int(metadata.get("state_end_page", ""))
    except (TypeError, ValueError):
        return []
    if start_page <= 0 or end_page < start_page:
        return []
    return [
        page_index
        for page_index, page in enumerate(doc)
        if (page_number := report_page_number(page)) is not None
        and start_page <= page_number <= end_page
    ]


def locate_state_summary(doc: fitz.Document, allowed_page_indices: set[int]) -> int:
    all_sizes: list[float] = []
    pages: list[tuple[int, list[dict[str, Any]]]] = []
    for page_index in sorted(allowed_page_indices):
        page = doc[page_index]
        lines = page_lines(page)
        pages.append((page_index, lines))
        all_sizes.extend(line["max_size"] for line in lines)

    typical_size = median(all_sizes) if all_sizes else 0.0
    for page_index, lines in pages:
        for line in lines:
            compact_text = normalize(line["text"]).replace(" ", "")
            if compact_text != "STATESUMMARY":
                continue
            if line["is_bold"] and line["max_size"] >= max(14.0, typical_size + 2.0):
                return page_index
    raise RuntimeError('Could not locate visually primary "STATE SUMMARY" heading.')


def collect_table_items(doc: fitz.Document, first_page: int, allowed_page_indices: set[int]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page_index in sorted(index for index in allowed_page_indices if index >= first_page):
        lines = page_lines(doc[page_index])
        if page_index > first_page and any(
            line["text"].replace(" ", "") == "STATESEARCHRESULTS" for line in lines
        ):
            break

        offset = (page_index - first_page) * PAGE_STRIDE
        for block in doc[page_index].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans: list[dict[str, Any]] = []
                for span in line.get("spans", []):
                    text = normalize(span.get("text", ""))
                    if not text:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    spans.append(
                        {
                            "text": text,
                            "is_bold": "bold" in span.get("font", "").lower(),
                            "x0": float(x0),
                            "y0": float(y0) + offset,
                            "x1": float(x1),
                            "y1": float(y1) + offset,
                        }
                    )
                if spans:
                    items.append(
                        {
                            "text": normalize(" ".join(span["text"] for span in spans)),
                            "spans": spans,
                            "x0": min(span["x0"] for span in spans),
                            "y0": min(span["y0"] for span in spans),
                        }
                    )
    return sorted(items, key=lambda item: (item["y0"], item["x0"]))


def extract_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    status_pattern = re.compile(r"^(Registered|Renewed|Cancelled|Expired|Abandoned)\b")
    status_lines = [
        item
        for item in items
        if 245 <= item["x0"] <= 320 and status_pattern.match(item["text"])
    ]

    rows: list[dict[str, str]] = []
    for index, status_line in enumerate(status_lines):
        row_top = status_line["y0"]
        row_bottom = status_lines[index + 1]["y0"] if index + 1 < len(status_lines) else items[-1]["y0"] + 1

        ref_lines = [
            item
            for item in items
            if 35 <= item["x0"] < 250
            and row_top - 1 <= item["y0"] < row_bottom - 1
            and not item["text"].replace(" ", "").startswith("STATESUMMARY")
            and not item["text"].startswith(
                (
                    "THE MAGNUM ICE CREAM COMPANY",
                    TARGET_HEADING_TEXT,
                    "S T A T E  S U M M A R Y",
                    "State Trademark References",
                    "©",
                    "Â©",
                    "Page ",
                )
            )
        ]

        mark_parts: list[str] = []
        owner_parts: list[str] = []
        for line in ref_lines:
            for span in line["spans"]:
                if span["is_bold"]:
                    mark_parts.append(span["text"])
                else:
                    owner_parts.append(span["text"])

        class_lines = [
            item["text"]
            for item in items
            if 345 <= item["x0"] <= 410 and abs(item["y0"] - row_top) < 3
        ]

        rows.append(
            {
                "mark_text": normalize(" ".join(mark_parts)),
                "owner_name": normalize(" ".join(owner_parts)),
                "status": normalize(status_line["text"]),
                "intl_class": normalize(class_lines[0]) if class_lines else "",
            }
        )
    return rows


def main() -> None:
    require_conda_env()
    doc = fitz.open(PDF_PATH)
    allowed_page_indices = set(layer1_state_page_indices(doc, PDF_PATH))
    if not allowed_page_indices:
        print(json.dumps({"state_summary_data": []}, indent=2, ensure_ascii=False))
        return
    first_page = locate_state_summary(doc, allowed_page_indices)
    rows = extract_rows(collect_table_items(doc, first_page, allowed_page_indices))
    print(json.dumps({"state_summary_data": rows}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
