from shared.models import RETRIEVAL_QUERY_MAX_CHARS


def bounded_retrieval_query(draft: str) -> str:
    """Bound exact or speculative retrieval text without losing both ends."""

    query = draft.strip()
    if len(query) <= RETRIEVAL_QUERY_MAX_CHARS:
        return query
    separator = "\n...\n"
    remaining = RETRIEVAL_QUERY_MAX_CHARS - len(separator)
    prefix_chars = remaining // 2
    suffix_chars = remaining - prefix_chars
    return f"{query[:prefix_chars].rstrip()}{separator}{query[-suffix_chars:].lstrip()}"
