class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited; retry after {retry_after:.2f}s")
        self.retry_after = retry_after
