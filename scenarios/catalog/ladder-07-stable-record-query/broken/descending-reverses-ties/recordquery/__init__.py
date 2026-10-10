def query(records, *, status=None, sort_by="score", descending=False, offset=0, limit=None):
    if sort_by not in ("score", "name"):
        raise ValueError("unsupported sort key")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be nonnegative")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be nonnegative or None")
    selected = [record for record in records if status is None or record.get("status") == status]
    present = [record for record in selected if record.get(sort_by) is not None]
    missing = [record for record in selected if record.get(sort_by) is None]
    result = (
        list(reversed(sorted(present, key=lambda record: record[sort_by])))
        if descending
        else sorted(present, key=lambda record: record[sort_by])
    ) + missing
    return result[offset:] if limit is None else result[offset : offset + limit]
