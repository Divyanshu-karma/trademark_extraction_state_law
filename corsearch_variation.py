# image etraction connected to  corsearch.py
import argparse
import json
from pathlib import Path
from typing import Any

import fitz

import state_image
from corsearch import (
    DEFAULT_PDF_PATH,
    is_state_search_results_page,
    pages_by_footer_range,
    timestamped_output_path,
    extract_state_page,
)
from layer1extraction import extract_pdf


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
            row["state_image_path"] = image_result["state_image_path"]
            row["Image_Base64"] = image_result["Image_Base64"]
            state_summary_data.append(row)

    return {"state_summary_data": state_summary_data}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract CORSEARCH state trademark result pages with state images.")
    parser.add_argument("pdf_path", nargs="?", default=DEFAULT_PDF_PATH, help="Input CORSEARCH PDF path.")
    parser.add_argument("-o", "--output", help="Optional output JSON path. Defaults to corsearch_result timestamped filename.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = extract_corsearch_state_summary(args.pdf_path)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    output_path = Path(args.output) if args.output else timestamped_output_path(args.pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
