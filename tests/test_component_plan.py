"""Generated component ownership and bounded flow admission, without providers."""
import copy
import unittest

import autocode_component_plan as policy
import autocode_goal_lifecycle as lifecycle
import autocode_multicomponent as components
from goal_fixtures import body


class ComponentPlanTests(unittest.TestCase):
    def setUp(self):
        component = components.Component("store", "HTTP notes", ("R1",), (), (), ())
        self.task = components.component_brief(component, components.Architecture())
        self.root = "components/store"
        self.body = body()
        paths = ["components/store/server.py", "components/store/tests/test_notes.py"]
        self.body["milestones"][0]["affected_paths"] = paths[:]
        self.body["deliverables"] = paths[:]
        self.body["initial_task"] = {"kind": "implement", "milestone_id": "M1", "objective": "Serve notes",
            "affected_paths": paths[:], "requirements": ["Serve notes"], "acceptance_criteria": ["C1"],
            "validation_plan": ["Start the real service subprocess; health, POST, GET"]}
        self.body["end_to_end_flow"] = ["Start the real components/store/server.py subprocess with PORT set.",
            "GET /health, POST /notes and GET /notes/1 over real local HTTP; verify statuses and JSON."]
        self.body["permission_boundaries"] = ["No network access: local sockets only; the Docker base image "
            "is referenced but the image is not built in this run."]

    def validate(self):
        before = copy.deepcopy(self.body)
        policy.validate(self.task, self.root, self.body)
        lifecycle.validate_body({"task": self.task, "settings": {"regression": {"test_root": self.root}}}, self.body)
        self.assertEqual(before, self.body)

    def test_literal_owned_paths_and_read_only_inputs_are_accepted(self):
        self.body["milestones"][0]["affected_paths"] = ["components/store/", "components/store/tests"]
        self.body["technical_approach"] = ["Read architecture/contracts/note.schema.json and design/reference.png."]
        self.body["deliverables"] += ["Service matching architecture/contracts/note.schema.json",
                                     "HTTP/JSON", "OpenAPI/Swagger", "API.v1"]
        self.body["scope_exclusions"] = ["Compose and cross-service smoke belong to separate integration."]
        self.validate()

    def test_all_milestones_initial_task_and_literal_outputs_are_checked(self):
        bad = ["server.py", "components/gateway/server.py", "components/storehouse/server.py",
               "components/store/../gateway/server.py", "/components/store/server.py",
               "components/store/**/*.py", "components/store/./server.py", "components/store//server.py",
               "components/store\\server.py", "components/store/test?.py", "*", ".gitignore", ".env",
               "server.py ", " components/store/server.py", "components/store/server.py\n",
               "components/store/my server.py", "/tmp/marker", "C:/tmp/marker", "tests/", "src/", "docs/"]
        for location in ("milestone", "second milestone", "initial_task", "deliverable"):
            for path in bad:
                with self.subTest(location=location, path=path):
                    proposed = copy.deepcopy(self.body)
                    if location == "milestone":
                        proposed["milestones"][0]["affected_paths"] = [path]
                    elif location == "second milestone":
                        proposed["milestones"].append({"id": "M2", "affected_paths": [path]})
                    elif location == "initial_task":
                        proposed["initial_task"]["affected_paths"] = [path]
                    else:
                        proposed["deliverables"] = [path]
                    before = copy.deepcopy(proposed)
                    with self.assertRaisesRegex(ValueError, "Component plan"):
                        policy.validate(self.task, self.root, proposed)
                    self.assertEqual(before, proposed)

    def test_generic_test_root_and_model_claims_do_not_create_a_write_boundary(self):
        generic = body()
        generic["constraints"] = ["Own only components/store/", self.task]
        policy.validate("Build a greeting CLI", self.root, generic)
        lifecycle.validate_body({"task": "Build a greeting CLI", "settings": {
            "regression": {"test_root": self.root}}}, generic)

    def test_legacy_missing_root_uses_original_caller_ownership_without_mutation(self):
        state = {"task": self.task, "settings": {"regression": {}}, "user_events": []}
        before = copy.deepcopy(state)
        policy.validate(self.task, None, self.body)
        lifecycle.validate_body(state, self.body)
        self.assertEqual(self.root, policy.effective_root(self.task, None))
        self.assertEqual(before, state)
        self.assertIsNone(policy.effective_root("Build a greeting CLI", None))

    def test_generated_binding_mismatch_and_ambiguous_fail_closed(self):
        ownership = "Own only the directory components/store/; do not create or edit any file outside it."
        for task, root in [(self.task, "components/gateway"), (self.task, ""),
                           (self.task, False), (self.task, 1), (self.task, []),
                           (self.task, "components/store/../store"),
                           (self.task.replace("components/store/;", "components/gateway/;"), self.root),
                           (self.task.replace(ownership, ""), self.root), (self.task + " " + ownership, self.root),
                           (self.task + " Implement the gateway component of a larger system: HTTP service.", self.root),
                           (self.task.replace("Implement the store", "Implement the ../store"), self.root),
                           (self.task.replace("larger system:", "larger system;"), self.root)]:
            with self.subTest(task=task, root=root), self.assertRaisesRegex(ValueError, "Component plan"):
                policy.validate(task, root, self.body)
        for task in (self.task.replace(ownership, ""), self.task + " " + ownership,
                     self.task.replace("components/store/;", "components/gateway/;"),
                     self.task.replace("components/store/;", "components/store//;")):
            with self.subTest(legacy_task=task), self.assertRaisesRegex(ValueError, "Component plan"):
                policy.validate(task, None, self.body)

    def test_observed_and_quoted_direct_child_deferrals_are_rejected(self):
        steps = ["`docker build -f components/store/Dockerfile components/store/` builds the store image "
                 "(exercised at integration time; this run verifies the Dockerfile by inspection).",
                 "Build the Docker image, deferred to separate integration.",
                 "The gateway (built separately) proxies these calls at integration time per architecture/smoke.json.",
                 "Run the container during later integration.", "Run the cross-service smoke, deferred until integration.",
                 "docker build components/store/, deferred to separate integration.",
                 "Build the Docker image, exercised at integration time.",
                 "Run the container at integration time.", "Run the Compose smoke at integration time."]
        steps += [prefix + "`docker build components/store/` " + suffix
                  for prefix in ("", " \t ") for suffix in ("deferred to separate integration.", "at integration time.")]
        for step in steps:
            with self.subTest(step=step):
                self.body["end_to_end_flow"] = [step]
                with self.assertRaisesRegex(ValueError, "explicitly deferred"):
                    self.validate()

    def test_operation_wide_bans_and_affirmative_conjunctions_are_enforced(self):
        cases = [("docker build --network=none components/store/", "No docker build."),
                 ("Build the Docker image", "Do not build the Docker image."),
                 ("Run the container", "Do not run the container."), ("docker compose up", "No docker compose."),
                 ("The gateway proxies requests", "No gateway proxying."),
                 ("docker run --rm -e PORT=8001 store; verify health POST GET.", "Do not run the container."),
                 ("Build the Docker image (do not run docker build until integration).", "No docker build.")]
        cases += [(text, "No docker build.") for text in (
            "Run the container and build the Docker image.", "Run the container, and build the Docker image.",
            "Do not run the container, and build the Docker image.",
            "Do not run docker build for the gateway, but build the Docker image for this store.",
            '`docker run --env "MESSAGE=and build the Docker image" store` is not required in this run and build the Docker image.',
            '`docker run --env "MESSAGE=and build the Docker image" store` is not required in this run, and build the Docker image.')]
        for step, boundary in cases:
            with self.subTest(step=step):
                self.body["end_to_end_flow"] = [step]
                self.body["permission_boundaries"] = [boundary]
                with self.assertRaisesRegex(ValueError, "explicitly prohibited"):
                    self.validate()

    def test_inspection_negation_descriptive_builds_and_quoted_arguments_are_not_execution(self):
        texts = ["Inspect components/store/Dockerfile to verify that docker build uses only components/store/ as context.",
                 "`docker build` is not required in this run; verify local health, POST, GET.",
                 "`docker build components/store/` is not required in this run.",
                 " \t `docker build components/store/` is not executed in this run.",
                 "Docker build produces an image whose CMD starts the service.",
                 "docker build produces an image whose CMD starts the service.",
                 "Do not run the Compose smoke here; start the real server and verify health, POST, GET.",
                 'docker run --env "MESSAGE=and build the Docker image" store',
                 'Run the container with label "and build the Docker image"; verify health.']
        for text in texts:
            with self.subTest(text=text):
                self.body["end_to_end_flow"] = [text]
                self.body["permission_boundaries"] = ["No docker build."]
                self.body["scope_exclusions"] = ["Run the Compose smoke at integration time."]
                self.validate()

    def test_targeted_bans_parent_declarations_and_unrelated_deferrals_do_not_block_child(self):
        self.body["end_to_end_flow"] = ["Build the Docker image locally; authentication deferred to later integration.",
            "Build the Docker image locally, authentication deferred to later integration.",
            "Build the Docker image locally (authentication deferred to later integration)."]
        self.body["permission_boundaries"] = ["Do not run docker build for the gateway."]
        self.body["scope_exclusions"] = ["Docker build is deferred to later integration."]
        self.validate()
        proposed = copy.deepcopy(self.body)
        proposed["milestones"][0]["affected_paths"] = ["components/gateway/"]
        proposed["initial_task"]["affected_paths"] = ["components/gateway/"]
        proposed["deliverables"] = ["components/gateway/server.py"]
        proposed["end_to_end_flow"] = ["Test the gateway proxy against a real local HTTP fixture; verify POST and GET."]
        proposed["scope_exclusions"] = ["The gateway proxies to the real store at integration time."]
        policy.validate(self.task.replace("store", "gateway"), "components/gateway", proposed)
