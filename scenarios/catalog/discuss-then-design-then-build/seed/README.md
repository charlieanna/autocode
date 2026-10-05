# metadata-api

Serves registry metadata to internal tools. Runs under gunicorn with the
configuration in `deploy/gunicorn.conf.py`. Decision records live in
`docs/decisions/`, designs in `docs/design/`. Tests:
`python3 -m unittest discover -s tests -t .` (standard library only).
