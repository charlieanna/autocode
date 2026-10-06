# events

Registry event processing. The registry gateway inserts every registry event
into the Postgres queue table `registry_events`; one consumer reads it in
insertion order and hands each event to billing and notifications
(`events/processor.py`). Proposals for changing this live in `docs/design/`.

Run the tests with `python3 -m unittest discover -s tests -t .`.
