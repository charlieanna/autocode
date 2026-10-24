"""Read-only composition of core attention with local acknowledgement metadata."""

try:
    from . import autocode_attention as attention
    from . import autocode_attention_store as store
except ImportError:
    import autocode_attention as attention
    import autocode_attention_store as store


def observed(value):
    if not isinstance(value, dict):
        return value
    item = value.get("item")
    if not item:
        return value
    return attention.with_acknowledgement(value, store.inspect(item["id"], item["workspace"], item["run_dir"]))
