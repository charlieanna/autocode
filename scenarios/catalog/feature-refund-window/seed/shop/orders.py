"""Orders as the store records them."""

from dataclasses import dataclass, field


@dataclass
class Order:
    id: str
    paid_cents: int
    delivered_at: int | None = None  # Unix seconds, UTC; None until delivered
    disputed: bool = False
    refunds: list[int] = field(default_factory=list)

    @property
    def refunded_cents(self) -> int:
        return sum(self.refunds)
