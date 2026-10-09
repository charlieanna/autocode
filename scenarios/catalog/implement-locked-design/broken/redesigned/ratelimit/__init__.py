"""Token-bucket rate limiting."""
from .limiter import RateLimiter, TokenBucket

LimiterRegistry = RateLimiter

__all__ = ["TokenBucket", "LimiterRegistry", "RateLimiter"]
