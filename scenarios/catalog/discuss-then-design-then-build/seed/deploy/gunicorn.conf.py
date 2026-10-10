# Production server configuration. Each worker is a separate OS process with
# its own interpreter and memory; workers share nothing.
bind = "0.0.0.0:8000"
workers = 4
max_requests = 2000  # recycle a worker after this many requests (fresh process)
max_requests_jitter = 200
timeout = 30
# A host-local directory every worker can read and write. Nothing uses it yet.
raw_env = ["METADATA_CACHE_DIR=/var/cache/metadata-api"]
