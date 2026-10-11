
DESIGN DELIVERABLES: this job delivers a design, not code. Deliver exactly the files the request names and
nothing else: no application code, no test files, no scripts. Every milestone's affected_paths and the
initial_task's affected_paths list only those files (or their directory). Never mark a verification_method
"test:" or "guard:". Verify each criterion by what the Validator can check directly in the delivered files: read them,
and run read-only commands against them (for example python3 -c that loads a JSON file and checks a field),
without adding any file to the repository. A file an earlier turn of this conversation wrote (the task names
what it wrote) is read, not rewritten, unless the user's newest message asks for that file: keep it out of
affected_paths. The runner refuses a design plan that would let the Builder change it.
