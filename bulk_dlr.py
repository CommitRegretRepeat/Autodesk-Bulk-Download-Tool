from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import requests
from pypdf import PdfReader


# Matches attachment-looking filenames inside the "Final Response" attachments text block
FILE_REGEX = re.compile(
    r"[A-Za-z0-9_\-(). &]+\.(pdf|msg|docx|doc|xlsx|xls|zip|jpg|jpeg|png|eml)",
    re.IGNORECASE,
)

# Content-Disposition filename extractor (basic)
CD_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)

ALLOWED_INPUT_FILE_EXTS = {".pdf"}

DEFAULT_HEADERS = {
    "User-Agent": "ACC-FinalResponse-Downloader/1.0",
}


def print_script_explanation() -> None:
    explanation = """
This script bulk downloads "Final Response" attachments from Autodesk Construction Cloud Submittals exports.

REQUIRED FILE TYPE
You must supply an ACC export named "Submittal item detail" (PDF).

How to generate the PDF in ACC
ACC -> Submittals -> select submittals -> Export -> Export as "Submittal item detail"

What the script does
1) For each PDF, scans for the "Final Response" section and its "Attachments" block.
2) Extracts Autodesk attachment URLs from the PDF link annotations.
3) Pairs attachment URLs to attachment names from the "Final Response" block.
4) Downloads each attachment.
5) Normalises filenames and prevents duplicates by appending _1, _2, etc.

Output behaviour
- You choose an output base folder
- The script creates ONE new folder named after the input file or input directory
- All attachments from all PDFs are saved into that one folder
""".strip()

    print(explanation)
    print()


def get_filename_from_content_disposition(
    headers: requests.structures.CaseInsensitiveDict,
) -> Optional[str]:
    cd = headers.get("Content-Disposition", "")
    if not cd:
        return None
    m = CD_FILENAME_RE.search(cd)
    if not m:
        return None
    return requests.utils.unquote(m.group(1).strip())


def clean_attachment_name(raw: str) -> str:
    name = raw.strip().rstrip(",")
    cleaned = re.sub(r"(?i)^(?:final response\s+)?attachments\s+", "", name).strip()
    return cleaned or name


def is_valid_submittal_item_detail_pdf(pdf_path: Path) -> Tuple[bool, str]:
    if pdf_path.suffix.lower() not in ALLOWED_INPUT_FILE_EXTS:
        return False, f"Invalid file type: {pdf_path.suffix} (expected .pdf)"

    try:
        reader = PdfReader(str(pdf_path))
        if not reader.pages:
            return False, "PDF has no pages"

        text = reader.pages[0].extract_text() or ""

        required_markers = [
            "Submittal item detail",
            "Autodesk® Construction Cloud",
        ]

        missing = [m for m in required_markers if m not in text]
        if missing:
            return (
                False,
                "PDF does not look like an ACC 'Submittal item detail' export "
                f"(missing: {', '.join(missing)})",
            )

        return True, "OK"

    except Exception as exc:
        return False, f"Failed to read PDF: {exc}"


def validate_input_path(input_path: Path) -> Tuple[bool, str]:
    if not input_path.exists():
        return False, f"Path not found: {input_path}"

    if input_path.is_file():
        ok, msg = is_valid_submittal_item_detail_pdf(input_path)
        return ok, msg

    if input_path.is_dir():
        pdfs = list(input_path.rglob("*.pdf"))
        if not pdfs:
            return False, f"Directory contains no PDFs: {input_path}"

        for pdf in pdfs:
            ok, _ = is_valid_submittal_item_detail_pdf(pdf)
            if ok:
                return True, "OK"

        return False, "Directory contains PDFs, but none are valid ACC 'Submittal item detail' exports"

    return False, f"Input is neither a file nor a directory: {input_path}"


def prompt_for_valid_input_path(prompt_text: str) -> Path:
    while True:
        raw = input(prompt_text).strip().strip('"').strip("'")
        if not raw:
            print("Please enter a path.")
            continue
        p = Path(raw)
        ok, msg = validate_input_path(p)
        if ok:
            return p
        print(msg)


def prompt_for_output_base_folder(prompt_text: str) -> Path:
    while True:
        raw = input(prompt_text).strip().strip('"').strip("'")
        if not raw:
            print("Please enter a path.")
            continue
        p = Path(raw)
        try:
            p.mkdir(parents=True, exist_ok=True)
            if not p.is_dir():
                print(f"Output path is not a folder: {p}")
                continue
            return p
        except Exception as exc:
            print(f"Could not create/access folder '{p}': {exc}")


def format_display_name(name: str) -> str:
    cleaned = name.replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    words = []
    for word in cleaned.split():
        if word.isupper() and len(word) <= 3:
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


def normalise_filename(original_name: str) -> str:
    stem, dot, ext = original_name.rpartition(".")
    base_name = stem if dot else original_name
    extension = f".{ext}" if dot else ""

    code_match = re.search(r"(?<!\d)(\d{2}\s?\d{2}\s?\d{2})(?!\d)", base_name)
    if not code_match:
        cleaned = format_display_name(base_name)
        return f"{cleaned}{extension}" if extension else cleaned

    code_raw = code_match.group(1)
    digits = code_raw.replace(" ", "")
    digits = digits[:6].ljust(6, "0") if len(digits) >= 6 else digits.zfill(6)
    formatted_code = f"{digits[0:2]} {digits[2:4]} {digits[4:6]}"

    start, end = code_match.span(1)

    i = end
    while i < len(base_name) and base_name[i] in " .-0123456789":
        i += 1

    rest = base_name[i:].lstrip(" -._")
    cleaned_rest = format_display_name(rest) if rest else ""

    if cleaned_rest:
        new_name = f"{formatted_code} - {cleaned_rest}"
    else:
        new_name = formatted_code

    return f"{new_name}{extension}" if extension else new_name


def safe_filename(name: str, existing: Set[str]) -> str:
    if not name:
        name = "download.bin"
    name = name.replace("\\", "_").replace("/", "_")

    filename = name
    counter = 1
    while filename in existing:
        stem, dot, ext = name.rpartition(".")
        if dot:
            filename = f"{stem}_{counter}.{ext}"
        else:
            filename = f"{name}_{counter}"
        counter += 1

    existing.add(filename)
    return filename


def extract_final_response_attachment_links(pdf_path: Path) -> List[Tuple[str, Optional[str]]]:
    reader = PdfReader(str(pdf_path))
    results: list[tuple[str, Optional[str]]] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        final_attachment_names: list[str] = []

        for idx, line in enumerate(lines):
            if not line.startswith("Attachments"):
                continue

            has_final_response = any(
                j >= 0 and lines[j].startswith("Final Response")
                for j in (idx - 1, idx - 2)
            )
            if not has_final_response:
                continue

            block_parts = [line]
            next_idx = idx + 1
            while next_idx < len(lines):
                next_line = lines[next_idx]
                if next_line.startswith(
                    (
                        "Final Response",
                        "Package",
                        "Submittal item detail",
                        "Ball in court",
                        "Submitted",
                        "Sent for review",
                        "Review Step",
                        "References and Attachments",
                    )
                ):
                    break
                block_parts.append(next_line)
                next_idx += 1

            block_text = " ".join(block_parts)
            for m in FILE_REGEX.finditer(block_text):
                raw_name = m.group(0)
                name = clean_attachment_name(raw_name)
                final_attachment_names.append(name)

        if not final_attachment_names:
            continue

        autodesk_urls: list[str] = []
        if "/Annots" in page:
            for annot in page["/Annots"]:
                obj = annot.get_object()
                if "/A" in obj and "/URI" in obj["/A"]:
                    url = obj["/A"]["/URI"]
                    if (
                        isinstance(url, str)
                        and "developer.api.autodesk.com" in url
                        and "reports/v2/attachments" in url
                    ):
                        autodesk_urls.append(url)

        if not autodesk_urls:
            continue

        n = len(final_attachment_names)
        final_urls = autodesk_urls[-n:] if len(autodesk_urls) >= n else autodesk_urls

        for i, url in enumerate(final_urls):
            suggested_name = final_attachment_names[i] if i < len(final_attachment_names) else None
            results.append((url, suggested_name))

    return results


def download_file(
    url: str,
    display_name: Optional[str],
    out_dir: Path,
    existing: Set[str],
    rename: bool = True,
    retries: int = 3,
) -> Optional[str]:
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=60, headers=DEFAULT_HEADERS) as r:
                r.raise_for_status()

                name: Optional[str] = display_name.strip() if display_name else None

                if not name:
                    cd_name = get_filename_from_content_disposition(r.headers)
                    if cd_name:
                        name = cd_name

                if not name:
                    base_part = url.split("?", 1)[0].rstrip("/")
                    name = base_part.rsplit("/", 1)[-1] or "download.bin"
                    if "." not in name:
                        name += ".bin"

                final_name = normalise_filename(name) if rename else name
                final_name = safe_filename(final_name, existing)

                dest = out_dir / final_name

                total = int(r.headers.get("content-length", 0))
                downloaded = 0

                print(f"      Saving as: {final_name}")

                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total > 0:
                            percent = (downloaded / total) * 100
                            print(f"\r      Downloading: {percent:6.2f}%", end="", flush=True)
                        else:
                            print("\r      Downloading: (no size info)", end="", flush=True)

                print("\r      Downloading: complete".ljust(40))
                return final_name

        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                sleep_s = 1.0 * attempt
                print(f"\n      Download failed (attempt {attempt}/{retries}): {exc}")
                print(f"      Retrying in {sleep_s:.1f}s...")
                time.sleep(sleep_s)
                continue
            break

    print(f"\n      ERROR downloading {url}: {last_exc}")
    return None


def gather_pdfs(input_path: Path) -> Iterable[Path]:
    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        yield input_path
        return

    if input_path.is_dir():
        yield from sorted(input_path.rglob("*.pdf"))
        return

    raise ValueError(f"Input {input_path} is neither a PDF file nor a directory containing PDFs.")


def process_pdf(pdf_path: Path, out_dir: Path, existing: Set[str]) -> None:
    print(f"\nProcessing: {pdf_path}")
    links = extract_final_response_attachment_links(pdf_path)

    if not links:
        print("  No Final Response Autodesk attachment links found.")
        return

    print(f"  Found {len(links)} Final Response attachment link(s).")

    total_links = len(links)
    for index, (url, display_name) in enumerate(links, start=1):
        print(f"  [{index}/{total_links}] URL: {url}")
        print(f"      Display text: {display_name}" if display_name else "      No display text; will infer filename.")
        download_file(url, display_name, out_dir, existing)


def resolve_paths(argv: Optional[List[str]]) -> Tuple[Path, Path]:
    parser = argparse.ArgumentParser(
        description="Bulk download 'Final Response' attachments from ACC 'Submittal item detail' PDFs."
    )
    parser.add_argument("input_path", type=Path, nargs="?", help="Path to a single PDF or a directory of PDFs.")
    parser.add_argument(
        "output_base",
        type=Path,
        nargs="?",
        help="Destination BASE folder. The script will create a subfolder named after the input.",
    )
    args = parser.parse_args(argv)

    # CLI mode
    if args.input_path and args.output_base:
        ok, msg = validate_input_path(args.input_path)
        if not ok:
            print(msg)
            sys.exit(1)
        return args.input_path, args.output_base

    # Interactive mode
    print_script_explanation()
    input_path = prompt_for_valid_input_path("Enter input PDF or folder: ")
    output_base = prompt_for_output_base_folder("Enter output base folder: ")
    return input_path, output_base


def output_folder_name(input_path: Path) -> str:
    # File => stem, Directory => name (more intuitive)
    return input_path.stem if input_path.is_file() else input_path.name


def main(argv: Optional[List[str]] = None) -> None:
    input_path, output_base = resolve_paths(argv)

    try:
        output_base.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(f"Could not create/access output base folder '{output_base}': {exc}")
        sys.exit(1)

    try:
        pdfs = list(gather_pdfs(input_path))
    except ValueError as e:
        print(e)
        sys.exit(1)

    if not pdfs:
        print(f"No PDFs found in {input_path}")
        sys.exit(1)

    # Single output folder for all PDFs
    download_folder = output_base / output_folder_name(input_path)
    download_folder.mkdir(parents=True, exist_ok=True)

    print(f"\nAll files will be saved to: {download_folder}\n")

    existing: Set[str] = set()

    # If directory input, skip invalid PDFs instead of failing hard
    for pdf in pdfs:
        ok, msg = is_valid_submittal_item_detail_pdf(pdf)
        if not ok:
            print(f"\nSkipping (invalid export): {pdf.name} -> {msg}")
            continue

        process_pdf(pdf, download_folder, existing)


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {e}")
        input("Press Enter to exit...")
        raise