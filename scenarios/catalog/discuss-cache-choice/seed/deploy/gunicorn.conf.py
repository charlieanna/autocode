# Production server configuration. Each worker is a separate OS process with
# its own interpreter and memory; workers share nothing.
bind = "0.0.0.0:8000"
workers = 4
max_requests = 2000            # recycle a worker after this many requests (fresh process)
max_requests_jitter = 200
timeout = 30
