from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def render(template_path: str, ctx: dict) -> str:
    path = Path(template_path)
    env = Environment(loader=FileSystemLoader(str(path.parent)), autoescape=False)
    template = env.get_template(path.name)
    return template.render(**ctx)