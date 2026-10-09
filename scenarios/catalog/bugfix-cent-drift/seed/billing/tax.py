"""Sales tax."""

TAX_RATE = 0.0825


def tax_on(amount: float) -> float:
    return amount * TAX_RATE
