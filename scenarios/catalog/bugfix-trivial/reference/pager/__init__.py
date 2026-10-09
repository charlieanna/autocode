"""Split a sequence into fixed-size pages."""


def page_count(total: int, size: int) -> int:
    """How many pages of ``size`` items hold ``total`` items (a partial last page counts)."""
    if size <= 0:
        raise ValueError("page size must be positive")
    return -(-total // size)


def page(items, number: int, size: int):
    """Items on 1-based page ``number``."""
    if number < 1 or number > max(page_count(len(items), size), 1):
        raise IndexError(f"no page {number}")
    start = (number - 1) * size
    return list(items[start:start + size])


def pages(items, size: int):
    return [page(items, number, size) for number in range(1, page_count(len(items), size) + 1)]
