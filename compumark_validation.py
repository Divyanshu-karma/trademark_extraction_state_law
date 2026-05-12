#compumark_validation.py
import copy
import re
from dataclasses import dataclass, field
from typing import Any


ALLOWED_GROUPS = {"One", "Two", "Three", "Four", "Five"}
ST_RE = re.compile(r"^ST\s*[-\u2212]\s*(\d+)$")
GROUP_RE = re.compile(r"^Group\s*:\s*(One|Two|Three|Four|Five)$")
REGISTRATION_RE = re.compile(r"^[A-Za-z0-9._\-/\u2212]+$")
CLASS_RE = re.compile(r"\b([0-9]|[1-3]\d|4[0-5])\b")
YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}\b")

REQUIRED_PAGE_LABELS = {
    "State:",
    "Status:",
    "Goods/Services:",
    "Registrant:",
}
FIELD_LABEL_MAPPING = {
    "State:": "State",
    "Status:": "Status",
    "Registration No.:": "registration_no",
    "Registrant:": "owner_name",
    "Goods/Services:": "goods_services_description",
}
OPTIONAL_FIELD_LABEL_MAPPING = {
    "Design Phrase:": "design_phrase",
    "Manner Of Display:": "manner_of_display",
    "Filing Correspondent:": "filing_correspondent",
    "First Use In State:": "first_use_in_state",
}
MISSING_LABEL_WARNING_MAPPING = {
    "Registration No.:": "registration_no",
}
GOODS_STOP_LABELS = {
    "State Class:",
    "First Use In State:",
    "Registrant:",
    "Design Phrase:",
    "Manner Of Display:",
    "Filing Correspondent:",
    "First Use Anywhere:",
    "Disclaimer:",
    "Renewed To:",
    "Search:",
    "State Page:",
    "Analyst:",
}
KNOWN_LABELS = REQUIRED_PAGE_LABELS | set(FIELD_LABEL_MAPPING) | GOODS_STOP_LABELS | {
    "Date Registered:",
    "Renewed:",
    "Renewal",
    "International Class:",
}
STATE_NORMALIZATION_MAP = {
    "ALA": "ALABAMA",
    "ALA.": "ALABAMA",
    "ARIZ": "ARIZONA",
    "ARIZ.": "ARIZONA",
    "ARK": "ARKANSAS",
    "ARK.": "ARKANSAS",
    "CAL": "CALIFORNIA",
    "CAL.": "CALIFORNIA",
    "CALIF": "CALIFORNIA",
    "CALIF.": "CALIFORNIA",
    "COLO": "COLORADO",
    "COLO.": "COLORADO",
    "CONN": "CONNECTICUT",
    "CONN.": "CONNECTICUT",
    "FLA": "FLORIDA",
    "FLA.": "FLORIDA",
    "GA": "GEORGIA",
    "ILL": "ILLINOIS",
    "ILL.": "ILLINOIS",
    "IND": "INDIANA",
    "IND.": "INDIANA",
    "KAN": "KANSAS",
    "KAN.": "KANSAS",
    "KANS": "KANSAS",
    "KANS.": "KANSAS",
    "KY": "KENTUCKY",
    "LA": "LOUISIANA",
    "MASS": "MASSACHUSETTS",
    "MASS.": "MASSACHUSETTS",
    "MICH": "MICHIGAN",
    "MICH.": "MICHIGAN",
    "MINN": "MINNESOTA",
    "MINN.": "MINNESOTA",
    "MISS": "MISSISSIPPI",
    "MISS.": "MISSISSIPPI",
    "MO": "MISSOURI",
    "MONT": "MONTANA",
    "MONT.": "MONTANA",
    "NEB": "NEBRASKA",
    "NEB.": "NEBRASKA",
    "NEBR": "NEBRASKA",
    "NEBR.": "NEBRASKA",
    "NEV": "NEVADA",
    "NEV.": "NEVADA",
    "N H": "NEW HAMPSHIRE",
    "N.H.": "NEW HAMPSHIRE",
    "N J": "NEW JERSEY",
    "N.J.": "NEW JERSEY",
    "N M": "NEW MEXICO",
    "N.M.": "NEW MEXICO",
    "N Y": "NEW YORK",
    "N.Y.": "NEW YORK",
    "N CAR": "NORTH CAROLINA",
    "N. CAR.": "NORTH CAROLINA",
    "N DAK": "NORTH DAKOTA",
    "N. DAK.": "NORTH DAKOTA",
    "OKLA": "OKLAHOMA",
    "OKLA.": "OKLAHOMA",
    "ORE": "OREGON",
    "ORE.": "OREGON",
    "OREG": "OREGON",
    "OREG.": "OREGON",
    "PENN": "PENNSYLVANIA",
    "PENN.": "PENNSYLVANIA",
    "PA": "PENNSYLVANIA",
    "R I": "RHODE ISLAND",
    "R.I.": "RHODE ISLAND",
    "S CAR": "SOUTH CAROLINA",
    "S. CAR.": "SOUTH CAROLINA",
    "S DAK": "SOUTH DAKOTA",
    "S. DAK.": "SOUTH DAKOTA",
    "TENN": "TENNESSEE",
    "TENN.": "TENNESSEE",
    "TEX": "TEXAS",
    "TEX.": "TEXAS",
    "VA": "VIRGINIA",
    "W VA": "WEST VIRGINIA",
    "W. VA.": "WEST VIRGINIA",
    "WASH": "WASHINGTON",
    "WASH.": "WASHINGTON",
    "WIS": "WISCONSIN",
    "WIS.": "WISCONSIN",
    "WISC": "WISCONSIN",
    "WISC.": "WISCONSIN",
    "WYO": "WYOMING",
    "WYO.": "WYOMING",
}
US_STATES_AND_TERRITORIES = {
    "ALABAMA",
    "ALASKA",
    "ARIZONA",
    "ARKANSAS",
    "CALIFORNIA",
    "COLORADO",
    "CONNECTICUT",
    "DELAWARE",
    "DISTRICT OF COLUMBIA",
    "FLORIDA",
    "GEORGIA",
    "HAWAII",
    "IDAHO",
    "ILLINOIS",
    "INDIANA",
    "IOWA",
    "KANSAS",
    "KENTUCKY",
    "LOUISIANA",
    "MAINE",
    "MARYLAND",
    "MASSACHUSETTS",
    "MICHIGAN",
    "MINNESOTA",
    "MISSISSIPPI",
    "MISSOURI",
    "MONTANA",
    "NEBRASKA",
    "NEVADA",
    "NEW HAMPSHIRE",
    "NEW JERSEY",
    "NEW MEXICO",
    "NEW YORK",
    "NORTH CAROLINA",
    "NORTH DAKOTA",
    "OHIO",
    "OKLAHOMA",
    "OREGON",
    "PENNSYLVANIA",
    "RHODE ISLAND",
    "SOUTH CAROLINA",
    "SOUTH DAKOTA",
    "TENNESSEE",
    "TEXAS",
    "UTAH",
    "VERMONT",
    "VIRGINIA",
    "WASHINGTON",
    "WEST VIRGINIA",
    "WISCONSIN",
    "WYOMING",
    "PUERTO RICO",
    "GUAM",
    "AMERICAN SAMOA",
    "U.S. VIRGIN ISLANDS",
    "VIRGIN ISLANDS",
    "NORTHERN MARIANA ISLANDS",
}
ALLOWED_STATUSES = {
    "ABANDONED",
    "ACTIVE",
    "CANCELLED",
    "EXPIRED",
    "INACTIVE",
    "NOT RENEWED",
    "PENDING",
    "PUBLISHED",
    "REGISTERED",
    "RENEWED",
    "SUSPENDED",
    "WITHDRAWN",
}
IMAGE_SUCCESS_STATUS = "success"
IMAGE_NO_IMAGE_STATUS = "no_image_detected"
IMAGE_UPLOAD_FAILED_STATUS = "upload_failed"
IMAGE_BASE64_FAILED_STATUS = "base64_failed"
IMAGE_WARNING_STATUSES = {
    IMAGE_NO_IMAGE_STATUS,
    "render_failed",
    "unexpected_error",
}
ALLOWED_IMAGE_STATUSES = IMAGE_WARNING_STATUSES | {
    IMAGE_SUCCESS_STATUS,
    IMAGE_UPLOAD_FAILED_STATUS,
    IMAGE_BASE64_FAILED_STATUS,
}


@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recovered: bool = False

    def add_error(self, message: str) -> None:
        self.is_valid = False
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "recovered": self.recovered,
        }


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_label_variants(value: str) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"\s+:", ":", normalized)
    normalized = re.sub(r"[;：]\s*$", ":", normalized)
    normalized = re.sub(r":\s*$", ":", normalized)
    replacements = [
        (r"^StatePage\s*:", "State Page:"),
        (r"^State\s+Page\s*:", "State Page:"),
        (r"^Registration\s*No\.?\s*:", "Registration No.:"),
        (r"^RegistrationNo\s*:", "Registration No.:"),
        (r"^International\s*Class\s*:", "International Class:"),
        (r"^InternationalClass\s*:", "International Class:"),
        (r"^First\s*Use\s*In\s*State\s*:", "First Use In State:"),
        (r"^FirstUseInState\s*:", "First Use In State:"),
        (r"^Goods\s*/\s*Services\s*:", "Goods/Services:"),
        (r"^Goods/Services\s*:", "Goods/Services:"),
        (r"^Registrant\s*:", "Registrant:"),
        (r"^Manner\s*Of\s*Display\s*:", "Manner Of Display:"),
        (r"^MannerOfDisplay\s*:", "Manner Of Display:"),
        (r"^Filing\s*Correspondent\s*:", "Filing Correspondent:"),
        (r"^FilingCorrespondent\s*:", "Filing Correspondent:"),
        (r"^Design\s*Phrase\s*:", "Design Phrase:"),
        (r"^DesignPhrase\s*:", "Design Phrase:"),
        (r"^State\s*Class\s*:", "State Class:"),
        (r"^StateClass\s*:", "State Class:"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def canonical_label(text: str) -> str:
    return normalize_label_variants(text)


def normalize_state_value(value: Any) -> str:
    state = normalize_text(value).upper()
    if state in US_STATES_AND_TERRITORIES:
        return state
    compact_periods = re.sub(r"\s*\.\s*", ".", state).strip()
    compact_spaces = re.sub(r"\s+", " ", state.replace(".", " ")).strip()
    return (
        STATE_NORMALIZATION_MAP.get(state)
        or STATE_NORMALIZATION_MAP.get(compact_periods)
        or STATE_NORMALIZATION_MAP.get(compact_spaces)
        or state
    )


def line_texts(lines: list[dict[str, Any]]) -> list[str]:
    return [normalize_text(line.get("text", "")) for line in lines if normalize_text(line.get("text", ""))]


def canonical_line_texts(lines: list[dict[str, Any]]) -> list[str]:
    return [canonical_label(text) for text in line_texts(lines)]


def is_known_label(value: str) -> bool:
    text = canonical_label(value)
    if text in KNOWN_LABELS:
        return True
    return any(text.startswith(label + " ") for label in KNOWN_LABELS if label.endswith(":"))


def contains_alpha(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value or ""))


def st_candidates(lines: list[dict[str, Any]]) -> list[int]:
    candidates = []
    for text in canonical_line_texts(lines):
        match = ST_RE.match(text)
        if match:
            candidates.append(int(match.group(1)))
    return candidates


def group_candidates(lines: list[dict[str, Any]]) -> list[str]:
    candidates = []
    texts = canonical_line_texts(lines)
    for index, text in enumerate(texts):
        match = GROUP_RE.match(text)
        if match:
            candidates.append(match.group(1))
            continue
        if text == "Group:" and index + 1 < len(texts) and texts[index + 1] in ALLOWED_GROUPS:
            candidates.append(texts[index + 1])
    return candidates


def page_has_label(lines: list[dict[str, Any]], label: str) -> bool:
    canonical = canonical_label(label)
    for text in canonical_line_texts(lines):
        if text == canonical or text.startswith(canonical + " "):
            return True
    return False


def extract_inline_or_next(lines: list[dict[str, Any]], label: str) -> str:
    texts = canonical_line_texts(lines)
    canonical = canonical_label(label)
    for index, text in enumerate(texts):
        if text == canonical:
            for following in texts[index + 1 :]:
                if is_known_label(following):
                    return ""
                return normalize_text(following)
            return ""
        if text.startswith(canonical + " "):
            return normalize_text(text[len(canonical) :])
    return ""


def dedupe_classes(values: list[int]) -> list[int]:
    seen = set()
    deduped = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def extract_goods_services_structured(lines: list[dict[str, Any]]) -> tuple[list[int], str, str]:
    texts = canonical_line_texts(lines)
    start_index = next(
        (index for index, text in enumerate(texts) if text == "Goods/Services:" or text.startswith("Goods/Services: ")),
        None,
    )
    if start_index is None:
        return [], "", ""

    classes: list[int] = []
    description_parts: list[str] = []
    first_use = ""
    in_description = False

    inline_goods = ""
    if texts[start_index].startswith("Goods/Services: "):
        inline_goods = normalize_text(texts[start_index][len("Goods/Services:") :])

    for text in ([inline_goods] if inline_goods else []) + texts[start_index + 1 :]:
        if not text:
            continue
        if text.startswith("International Class:"):
            classes = dedupe_classes([int(value) for value in CLASS_RE.findall(text)])
            in_description = True
            continue
        if text.startswith("First Use In State:"):
            first_use = normalize_text(text.split(":", 1)[1])
            break
        if any(text == label or text.startswith(label + " ") for label in GOODS_STOP_LABELS):
            break
        if is_known_label(text) and not text.startswith("International Class:"):
            break
        if in_description:
            description_parts.append(text)

    if not first_use and page_has_label(lines, "First Use In State:"):
        first_use = extract_inline_or_next(lines, "First Use In State:")

    return classes, normalize_text(" ".join(description_parts)), first_use


def validate_page_structure(lines: list[dict[str, Any]], result: ValidationResult) -> None:
    st_values = st_candidates(lines)
    group_values = group_candidates(lines)
    if not st_values:
        result.add_error("page_structure: missing ST header")
    if len(st_values) > 1:
        result.add_error("page_structure: multiple ST candidates detected")
    if not group_values:
        result.add_error("page_structure: missing Group header")
    if "Search:" not in canonical_line_texts(lines) and not page_has_label(lines, "Search:"):
        result.add_error("page_structure: missing Search footer")
    if not page_has_label(lines, "State Page:"):
        result.add_error("page_structure: missing State Page footer")
    for label in REQUIRED_PAGE_LABELS:
        if not page_has_label(lines, label):
            result.add_error(f"page_structure: missing required label {label}")


def validate_st(row: dict[str, Any], lines: list[dict[str, Any]], result: ValidationResult) -> None:
    st_value = row.get("ST")
    if not isinstance(st_value, int) or st_value <= 0:
        result.add_error("field.ST: must be an integer greater than 0")
    values = st_candidates(lines)
    if len(values) != 1:
        result.add_error("field.ST: exactly one ST candidate must be detected")
    elif st_value != values[0]:
        result.add_error("field.ST: extracted ST does not match page header")


def validate_group(row: dict[str, Any], result: ValidationResult) -> None:
    if row.get("Group") not in ALLOWED_GROUPS:
        result.add_error("field.Group: invalid group value")


def validate_mark_text(row: dict[str, Any], result: ValidationResult) -> None:
    mark_text = normalize_text(row.get("mark_text", ""))
    if not mark_text:
        result.add_error("field.mark_text: empty mark text")
        return
    if is_known_label(mark_text):
        result.add_error("field.mark_text: mark text is a label")
    if not contains_alpha(mark_text):
        result.add_error("field.mark_text: mark text contains no alphabetic characters")
    for forbidden in ("State:", "Registrant:", "Goods/Services:"):
        if forbidden in mark_text:
            result.add_error(f"field.mark_text: contains forbidden label {forbidden}")


def validate_state(row: dict[str, Any], result: ValidationResult) -> None:
    state = normalize_state_value(row.get("State", ""))
    if state and state not in US_STATES_AND_TERRITORIES:
        result.add_error("field.State: not a canonical US state or territory")


def validate_status(row: dict[str, Any], result: ValidationResult) -> None:
    status = normalize_text(row.get("Status", "")).upper()
    if not status:
        return
    exact_match = status in ALLOWED_STATUSES
    prefix_match = any(
        status.startswith(allowed + " ") or status.startswith(allowed + " -")
        for allowed in sorted(ALLOWED_STATUSES, key=len, reverse=True)
    )
    if not exact_match and not prefix_match:
        result.add_error("field.Status: invalid status")


def validate_registration_no(row: dict[str, Any], result: ValidationResult) -> None:
    registration_no = normalize_text(row.get("registration_no", ""))
    if registration_no and not REGISTRATION_RE.match(registration_no):
        result.add_error("field.registration_no: invalid characters")


def validate_owner_name(row: dict[str, Any], lines: list[dict[str, Any]], result: ValidationResult) -> None:
    owner_name = normalize_text(row.get("owner_name", ""))
    if page_has_label(lines, "Registrant:") and not owner_name:
        result.add_error("field.owner_name: empty owner_name while Registrant label exists")
        return
    if owner_name:
        if is_known_label(owner_name):
            result.add_error("field.owner_name: owner_name is a label")
        if not contains_alpha(owner_name):
            result.add_error("field.owner_name: owner_name contains no alphabetic characters")


def validate_intl_class(row: dict[str, Any], result: ValidationResult) -> None:
    raw_values = row.get("intl_class", [])
    if not isinstance(raw_values, list):
        result.add_error("field.intl_class: must be a list")
        return
    for value in raw_values:
        if not isinstance(value, int) or value < 0 or value > 45:
            result.add_error("field.intl_class: all class values must be between 0 and 45")
            return


def validate_goods_services(row: dict[str, Any], lines: list[dict[str, Any]], result: ValidationResult) -> None:
    if page_has_label(lines, "Goods/Services:") and not normalize_text(row.get("goods_services_description", "")):
        result.add_error("field.goods_services_description: empty description while Goods/Services label exists")
    if page_has_label(lines, "International Class:") and not row.get("intl_class"):
        result.add_error("field.intl_class: empty classes while International Class label exists")
    first_use = normalize_text(row.get("first_use_in_state", ""))
    if page_has_label(lines, "First Use In State:"):
        if not first_use:
            result.add_error("field.first_use_in_state: empty while First Use In State label exists")
        elif not YEAR_RE.search(first_use):
            result.add_error("field.first_use_in_state: missing year-like content")


def validate_optional_text_fields(row: dict[str, Any], result: ValidationResult) -> None:
    for field_name in ("design_phrase", "manner_of_display", "filing_correspondent"):
        value = normalize_text(row.get(field_name, ""))
        if not value:
            continue
        if is_known_label(value):
            result.add_error(f"field.{field_name}: value is a label")
        if len(value) > 4000:
            result.add_error(f"field.{field_name}: value is unreasonably long")
    manner = normalize_text(row.get("manner_of_display", ""))
    if any(label in manner for label in ("Search:", "State Page:", "Analyst:")):
        result.add_error("field.manner_of_display: contaminated with footer labels")


def optional_label_missing_warning(
    row: dict[str, Any],
    lines: list[dict[str, Any]],
    result: ValidationResult,
    label: str,
    field_name: str,
) -> None:
    if normalize_text(row.get(field_name, "")):
        return
    if page_has_label(lines, label):
        return
    result.add_warning(f"field.{field_name}: label not present on page")


def add_validation_warnings(row: dict[str, Any], lines: list[dict[str, Any]], result: ValidationResult) -> None:
    mark_text = normalize_text(row.get("mark_text", ""))
    if 0 < len(mark_text) <= 2:
        result.add_warning("field.mark_text: unusually short mark text")
    if len(normalize_text(row.get("goods_services_description", ""))) > 2500:
        result.add_warning("field.goods_services_description: unusually long goods description")
    for label, field_name in OPTIONAL_FIELD_LABEL_MAPPING.items():
        optional_label_missing_warning(row, lines, result, label, field_name)
    for label, field_name in MISSING_LABEL_WARNING_MAPPING.items():
        optional_label_missing_warning(row, lines, result, label, field_name)
    if result.recovered:
        result.add_warning("validation: recovered row accepted")


def validate_completeness(row: dict[str, Any], lines: list[dict[str, Any]], result: ValidationResult) -> None:
    for label, field_name in FIELD_LABEL_MAPPING.items():
        if page_has_label(lines, label) and not normalize_text(row.get(field_name, "")):
            result.add_error(f"completeness: {field_name} is empty while {label} exists")


def image_field_exists(value: Any) -> bool:
    return normalize_text(value) not in {"", "not_exist"}


def validate_image_fields(row: dict[str, Any], result: ValidationResult) -> None:
    has_image_path = image_field_exists(row.get("state_image_path", "not_exist"))
    has_image_base64 = image_field_exists(row.get("Image_Base64", "not_exist"))
    image_status = normalize_text(row.get("image_status", ""))
    has_image = row.get("has_image")
    image_retry_attempted = bool(row.get("image_retry_attempted", False))
    image_recovered = bool(row.get("image_recovered", False))

    def add_image_error(message: str) -> None:
        if message not in result.errors:
            result.add_error(message)

    if image_status not in ALLOWED_IMAGE_STATUSES:
        add_image_error("image.validation: invalid image_status")
        return

    if not isinstance(has_image, bool):
        add_image_error("image.validation: inconsistent image state")
        return

    if has_image_path != has_image_base64:
        add_image_error("image.validation: inconsistent image state")

    if image_status == IMAGE_UPLOAD_FAILED_STATUS:
        add_image_error("image.validation: upload failed")
    if image_status == IMAGE_BASE64_FAILED_STATUS:
        add_image_error("image.validation: base64 conversion failed")

    if image_status == IMAGE_SUCCESS_STATUS:
        if not (has_image is True and has_image_path and has_image_base64):
            add_image_error("image.validation: inconsistent image state")
    elif image_status == IMAGE_NO_IMAGE_STATUS:
        if has_image is not False or has_image_path or has_image_base64:
            add_image_error("image.validation: inconsistent image state")
        else:
            result.add_warning(f"image.extraction: {image_status}")
    elif image_status in IMAGE_WARNING_STATUSES:
        if has_image is not False or has_image_path or has_image_base64:
            add_image_error("image.validation: inconsistent image state")
        else:
            result.add_warning(f"image.extraction: {image_status}")

    if image_retry_attempted and image_recovered:
        if image_status == IMAGE_SUCCESS_STATUS:
            result.add_warning("image.recovery: retry succeeded after initial failure")
        else:
            add_image_error("image.validation: inconsistent image state")

    if image_recovered and not image_retry_attempted:
        add_image_error("image.validation: inconsistent image state")


def validate_extracted_row(row: dict[str, Any], lines: list[dict[str, Any]]) -> ValidationResult:
    result = ValidationResult()
    validate_page_structure(lines, result)
    validate_st(row, lines, result)
    validate_group(row, result)
    validate_mark_text(row, result)
    validate_state(row, result)
    validate_status(row, result)
    validate_registration_no(row, result)
    validate_owner_name(row, lines, result)
    validate_intl_class(row, result)
    validate_goods_services(row, lines, result)
    validate_optional_text_fields(row, result)
    validate_completeness(row, lines, result)
    validate_image_fields(row, result)
    add_validation_warnings(row, lines, result)
    return result


def normalize_lines_for_recovery(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recovered_lines = copy.deepcopy(lines)
    for line in recovered_lines:
        line["text"] = normalize_label_variants(line.get("text", ""))
    return recovered_lines


def recover_extracted_row(row: dict[str, Any], lines: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    recovered_lines = normalize_lines_for_recovery(lines)
    recovered_row = dict(row)

    classes, description, first_use = extract_goods_services_structured(recovered_lines)
    if classes:
        recovered_row["intl_class"] = classes
    else:
        recovered_row["intl_class"] = dedupe_classes(
            [value for value in recovered_row.get("intl_class", []) if isinstance(value, int) and 0 <= value <= 45]
        )
    if description:
        recovered_row["goods_services_description"] = description
    if first_use:
        recovered_row["first_use_in_state"] = first_use

    for label, field_name in FIELD_LABEL_MAPPING.items():
        if not normalize_text(recovered_row.get(field_name, "")):
            recovered_value = extract_inline_or_next(recovered_lines, label)
            if recovered_value:
                recovered_row[field_name] = recovered_value

    if not normalize_text(recovered_row.get("filing_correspondent", "")):
        recovered_row["filing_correspondent"] = extract_inline_or_next(
            recovered_lines, "Filing Correspondent:"
        )

    registration_no = normalize_text(recovered_row.get("registration_no", ""))
    if registration_no:
        recovered_row["registration_no"] = registration_no.replace("\u2212", "-")

    return recovered_row, recovered_lines
