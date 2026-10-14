def greet(name, punctuation="!"):
    """Return a friendly greeting."""
    name = name.strip() or "there"
    return f"Hello, {name}{punctuation}"
