def coalesce(intervals):
    ordered = []
    for item in intervals:
        try:
            start, end = item
        except (TypeError, ValueError) as exc:
            raise ValueError("interval must have two endpoints") from exc
        if type(start) is not int or type(end) is not int or start >= end:
            raise ValueError("endpoints must be increasing integers")
        ordered.append((start, end))
    ordered.sort()
    result = []
    for start, end in ordered:
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result
