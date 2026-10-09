import base64
import binascii
import json


def paginate(rows, limit, cursor=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    after = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not cursor:
                raise ValueError("invalid cursor")
            decoded = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if not isinstance(decoded, list) or len(decoded) != 2 or any(type(value) is not int for value in decoded):
                raise ValueError("invalid cursor key")
            after = tuple(decoded)
        except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
            raise ValueError("invalid cursor") from error
    ordered = sorted(rows, key=lambda row: (row["created_at"], row["id"]))
    eligible = [row for row in ordered if after is None or (row["created_at"], row["id"]) > after]
    items = [dict(row) for row in eligible[:limit]]
    next_cursor = None
    if len(eligible) > limit:
        last = items[-1]
        key = [last["created_at"], last["id"]]
        next_cursor = base64.urlsafe_b64encode(json.dumps(key).encode()).decode()
    return {"items": items, "next_cursor": next_cursor}
