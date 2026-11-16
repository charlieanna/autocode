"""Sums and averages of order amounts."""

TAX_RATE = 0.08


def total(amounts):
    return sum(amounts)


def average(amounts):
    return total(amounts) / len(amounts)


def total_with_tax(amounts):
    return total(amounts) * TAX_RATE
