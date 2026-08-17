import markdown


def render_markdown(text):
    if not text:
        return ""
    return markdown.markdown(
        text,
        extensions=[
            "markdown.extensions.extra",
            "markdown.extensions.nl2br",
            "markdown.extensions.smarty",
            "markdown.extensions.fenced_code",
            "markdown.extensions.sane_lists",
        ],
    )
