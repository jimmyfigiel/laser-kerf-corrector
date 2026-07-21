"""Landing page and tool registry for the multi-tool web app. Each tool is
a Flask Blueprint registered here; adding a new one later is a two-line
change (import + append to TOOLS)."""

from __future__ import annotations

from flask import Flask

from . import feedback, kerf_tool

TOOLS = [
    {
        "name": "Laser Kerf Corrector",
        "description": "Compensate laser-cut SVG plans for kerf: shrink holes and grow or "
                        "shrink every other edge as needed so the finished part matches the drawing.",
        "url": kerf_tool.bp.url_prefix + "/",
    },
]

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Tools</title>
<style>
  html, body { margin: 0; min-height: 100%; font-family: system-ui, sans-serif; background: #1e1e1e; color: #ddd; }
  body { padding: 40px 20px; }
  h1 { font-size: 22px; margin: 0 0 24px; max-width: 720px; margin-left: auto; margin-right: auto; }
  .cards { max-width: 720px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }
  .card { display: block; padding: 16px 18px; background: #262626; border: 1px solid #383838; border-radius: 8px;
          color: #ddd; text-decoration: none; transition: border-color 0.15s, background 0.15s; }
  .card:hover { border-color: #6cf; background: #2a2f3a; }
  .card h2 { font-size: 15px; margin: 0 0 4px; color: #8fd0ff; }
  .card p { font-size: 13px; color: #aaa; margin: 0; line-height: 1.4; }
</style>
</head>
<body>
<h1>Tools</h1>
<div class="cards">
__CARDS__
</div>
</body>
</html>
"""

CARD_TEMPLATE = """<a class="card" href="{url}"><h2>{name}</h2><p>{description}</p></a>"""


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB; these are laser-plan SVGs, not photos
    app.register_blueprint(kerf_tool.bp)
    app.register_blueprint(feedback.bp)

    @app.route("/")
    def index():
        cards = "\n".join(CARD_TEMPLATE.format(**t) for t in TOOLS)
        return PAGE.replace("__CARDS__", cards)

    return app


app = create_app()
