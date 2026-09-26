"""WeasyPrint HTML → PDF renderer."""

from __future__ import annotations

from pathlib import Path

import weasyprint
from jinja2 import Environment, FileSystemLoader


_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def render_pdf(
    html_content: str,
    output_path: str,
    title: str = "Documentation",
    generated_at: str = "",
    version: int = 1,
) -> str:
    """
    Wrap *html_content* in the base document template, render to PDF at *output_path*.

    Args:
        html_content:  Inner HTML produced by the doc generator.
        output_path:   Destination file path (directories are created as needed).
        title:         Document title shown on the cover page and in running headers.
        generated_at:  ISO-8601 timestamp string displayed on the cover page.
                       Defaults to the current UTC time when empty.
        version:       1-based generation counter shown on the cover page so users
                       can distinguish successive re-generations of the same document.

    Returns the absolute path to the written PDF file.
    """
    if not generated_at:
        from datetime import datetime, timezone
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)
    template = env.get_template("doc_base.html")
    full_html = template.render(
        title=title,
        content=html_content,
        generated_at=generated_at,
        version=version,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    weasyprint.HTML(string=full_html).write_pdf(str(out))
    return str(out.resolve())
