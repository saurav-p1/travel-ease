from django import template

register = template.Library()


@register.filter
def absolute_uri(path, request):
    """Turn a relative path (from {% static %} or {% url %}) into an
    absolute, fully-qualified URL using the current request's scheme and
    host.

    FOUND DURING AUDIT: base.html's og:image/twitter:image/og:url tags
    were built with plain {% static %}/{% url %}, which only ever produce
    a relative path (e.g. "/static/images/og-image.jpg"). Open Graph and
    Twitter Card tags are read by an external crawler (Facebook, Twitter,
    WhatsApp, iMessage, Slack, etc.) fetching the page with no browser
    context to resolve a relative path against -- per the Open Graph
    protocol spec, these values MUST be absolute URLs. A relative one is
    silently ignored, so every link share of this site had no preview
    image at all. Usage: {{ some_relative_path|absolute_uri:request }}.
    """
    if not path:
        return path
    return request.build_absolute_uri(path)
