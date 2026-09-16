#!/usr/bin/env python3
"""Render an academic Markdown report to PDF with local image resolution."""

from __future__ import annotations

import argparse
import html
from pathlib import Path

import markdown
from weasyprint import CSS, HTML


# Extract the first level-one heading for HTML document metadata.
def document_title(markdown_text: str, fallback: str) -> str:
    for line in markdown_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


# Convert one Markdown file to a self-contained paginated PDF.
def render(markdown_path: Path, output_path: Path, css_path: Path, language: str) -> None:
    source = markdown_path.read_text(encoding="utf-8")
    title = document_title(source, markdown_path.stem)
    body = markdown.markdown(
        source,
        extensions=("extra", "tables", "toc", "sane_lists", "smarty"),
        output_format="html5",
    )
    document = (
        "<!doctype html>"
        f"<html lang=\"{html.escape(language)}\">"
        "<head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head>"
        f"<body>{body}</body></html>"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=document, base_url=str(markdown_path.parent)).write_pdf(
        str(output_path), stylesheets=[CSS(filename=str(css_path))]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("markdown", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--css", required=True, type=Path)
    parser.add_argument("--language", default="en")
    args = parser.parse_args()
    render(
        args.markdown.resolve(),
        args.output.resolve(),
        args.css.resolve(),
        args.language,
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
