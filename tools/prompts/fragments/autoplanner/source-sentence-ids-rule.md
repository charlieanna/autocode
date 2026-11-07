SOURCE SENTENCE IDS. source_sentences in the handoff splits every source into the sentences your criteria must
cover. Each has an id such as task:0#3 and requirement_ids, the requirement rows whose source_quote it supports.
Cite a whole sentence by putting its id in the citation's quote: {"source_id": "task:0", "quote": "task:0#3"}. The
runner replaces the id with the sentence's verbatim text before anything checks it, so do not copy the sentence
out. Quote text verbatim only for a span inside one sentence. Use requirement_ids to see which requirement rows a
sentence already evidences when you write requirement_trace.
