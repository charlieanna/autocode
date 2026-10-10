def query(records, *, status=None, sort_by="score", descending=False, offset=0, limit=None):
    return sorted(records, key=lambda record: record["score"])
