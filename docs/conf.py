"""Sphinx configuration for the documentation site (published to GitHub Pages).

The sources are the Markdown files under docs/ (MyST). `docs/README.md` is the GitHub-side index and
`docs/index.md` is the site's front page; `docs/concept/` is the initial concept record, outside the
current scope, so it is not published.
"""

project = "AI System Trading"
copyright = "AI System Trading contributors"
language = "ja"

extensions = [
    "myst_parser",
    "sphinxcontrib.mermaid",
    "sphinx_copybutton",
]

root_doc = "index"
exclude_patterns = ["_build", "README.md", "concept", "requirements.txt", "Thumbs.db", ".DS_Store"]

myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]

html_theme = "furo"
html_title = "AI System Trading ドキュメント"
html_search_language = "ja"
html_theme_options = {
    "source_repository": "https://github.com/wataruteduka1973/AI_System_Trading/",
    "source_branch": "main",
    "source_directory": "docs/",
}
