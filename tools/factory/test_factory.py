#!/usr/bin/env python3
"""Tests for the XRD/Composition factory.

Plain unittest so it runs with nothing installed but PyYAML:

    python3 tools/factory/test_factory.py
"""

import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crossplane_factory import Factory, SpecError, check_output  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXAMPLES = os.path.join(REPO, "tools", "factory", "examples")


def build(**overrides):
    """A minimal valid spec, with any part of it swapped out per test.

    When only `fields` is overridden the default composed resource follows
    along and reads the first declared field, so a test can focus on one input
    at a time.
    """
    spec = {
        "kind": "XThing",
        "group": "platform.hooli.tech",
        "provider": "aws",
        "fields": [{"name": "size", "type": "integer", "required": True}],
    }
    spec.update(overrides)
    if "resources" not in spec:
        first = spec["fields"][0]["name"]
        spec["resources"] = [{
            "name": "main",
            "apiVersion": "example.aws.m.upbound.io/v1beta1",
            "kind": "Example",
            "metadataName": False,
            "spec": {"forProvider": {first: {"fromField": first}}},
        }]
    return Factory(spec, "test-spec.yaml")


def template_of(factory):
    doc = yaml.safe_load(factory.render_composition())
    return doc["spec"]["pipeline"][0]["input"]["inline"]["template"]


def load_example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as handle:
        return Factory(yaml.safe_load(handle), "tools/factory/examples/%s" % name)


class TestSpecValidation(unittest.TestCase):
    def test_kind_must_be_upper_camel(self):
        with self.assertRaisesRegex(SpecError, "UpperCamelCase"):
            build(kind="xthing")

    def test_group_must_be_dns_name(self):
        with self.assertRaisesRegex(SpecError, "DNS name"):
            build(group="platform")

    def test_unknown_top_level_key_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "unknown top-level key"):
            build(compositions=[])

    def test_unknown_field_key_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "unknown key"):
            build(fields=[{"name": "size", "type": "integer", "minimun": 1}])

    def test_expression_must_reference_a_declared_field(self):
        with self.assertRaisesRegex(SpecError, "not declared under `fields`"):
            build(resources=[{
                "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
                "spec": {"x": {"fromField": "nope"}},
            }])

    def test_duplicate_resource_name_is_rejected(self):
        res = {"name": "main", "apiVersion": "v1", "kind": "ConfigMap"}
        with self.assertRaisesRegex(SpecError, "duplicate resource name"):
            build(resources=[res, dict(res)])

    def test_bounds_only_apply_to_numbers(self):
        with self.assertRaisesRegex(SpecError, "only applies to numeric"):
            build(fields=[{"name": "n", "type": "string", "minimum": 1}])

    def test_ready_replicas_needs_a_replicas_field(self):
        with self.assertRaisesRegex(SpecError, "needs a spec field"):
            build(resources=[{
                "name": "d", "apiVersion": "apps/v1", "kind": "Deployment", "ready": "replicas",
            }])

    def test_namespace_on_a_composed_resource_is_rejected(self):
        with self.assertRaisesRegex(SpecError, "XR's own namespace"):
            build(resources=[{
                "name": "main", "apiVersion": "v1", "kind": "ConfigMap", "namespace": "team-a",
            }])

    def test_a_composition_needs_at_least_one_resource(self):
        with self.assertRaisesRegex(SpecError, "non-empty list"):
            build(resources=[])


class TestTemplateBody(unittest.TestCase):
    def test_optional_field_is_guarded_with_with(self):
        factory = build(fields=[{"name": "size", "type": "integer"}])
        body = template_of(factory)
        self.assertIn("{{- with $spec.size }}", body)
        self.assertIn("size: {{ . }}", body)
        self.assertIn("{{- end }}", body)

    def test_required_field_is_rendered_inline(self):
        body = template_of(build())
        self.assertIn("size: {{ $spec.size }}", body)
        self.assertNotIn("{{- with", body)

    def test_defaulted_field_is_never_guarded_but_keeps_its_default(self):
        factory = build(fields=[{"name": "size", "type": "integer", "default": 3}])
        body = template_of(factory)
        self.assertIn("size: {{ $spec.size | default 3 }}", body)
        self.assertNotIn("{{- with", body)

    def test_explicit_optional_overrides_the_inference(self):
        factory = build(
            fields=[{"name": "size", "type": "integer", "required": True}],
            resources=[{
                "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
                "spec": {"size": {"fromField": "size", "optional": True}},
            }],
        )
        self.assertIn("{{- with $spec.size }}", template_of(factory))

    def test_strings_are_quoted_and_can_opt_out(self):
        factory = build(
            fields=[{"name": "image", "type": "string", "required": True}],
            resources=[{
                "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
                "spec": {"a": {"fromField": "image"}, "b": {"fromField": "image", "quote": False}},
            }],
        )
        body = template_of(factory)
        self.assertIn("a: {{ $spec.image | quote }}", body)
        self.assertIn("b: {{ $spec.image }}", body)

    def test_map_becomes_an_index_over_a_dict(self):
        factory = build(
            fields=[{"name": "location", "type": "string", "required": True, "enum": ["EU", "US"]}],
            resources=[{
                "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
                "spec": {"region": {"map": {"field": "location",
                                            "values": {"EU": "eu-north-1", "US": "us-east-2"}}}},
            }],
        )
        self.assertIn(
            'region: {{ index (dict "EU" "eu-north-1" "US" "us-east-2") $spec.location }}',
            template_of(factory),
        )

    def test_string_map_renders_a_guarded_range(self):
        factory = build(
            fields=[{"name": "tags", "type": "object", "additionalProperties": "string"}],
            resources=[{
                "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
                "spec": {"tags": {"fromField": "tags"}},
            }],
        )
        body = template_of(factory)
        self.assertIn("{{- range $key, $value := . }}", body)
        self.assertIn("{{ $key }}: {{ $value | quote }}", body)

    def test_raw_template_passes_through(self):
        factory = build(resources=[{
            "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
            "spec": {"x": {"template": "printf \"%s-suffix\" $name"}},
        }])
        self.assertIn('x: {{ printf "%s-suffix" $name }}', template_of(factory))

    def test_xr_is_only_bound_when_used(self):
        lean = template_of(build())
        self.assertIn("{{- $spec := .observed.composite.resource.spec -}}", lean)
        self.assertNotIn("$xr", lean)

        rich = template_of(build(resources=[{
            "name": "main", "apiVersion": "v1", "kind": "ConfigMap",
            "metadataName": {"fromXR": "name"},
        }]))
        self.assertIn("{{- $name := $xr.metadata.name -}}", rich)

    def test_ready_always_sets_the_annotation(self):
        factory = build(resources=[{
            "name": "main", "apiVersion": "v1", "kind": "ConfigMap", "ready": "always",
        }])
        self.assertIn('gotemplating.fn.crossplane.io/ready: "True"', template_of(factory))

    def test_ready_provider_leaves_readiness_to_auto_ready(self):
        self.assertNotIn("gotemplating.fn.crossplane.io/ready", template_of(build()))

    def test_ready_replicas_derives_from_observed_available_replicas(self):
        factory = build(
            fields=[{"name": "replicas", "type": "integer", "default": 1}],
            resources=[{
                "name": "deployment", "apiVersion": "apps/v1", "kind": "Deployment",
                "ready": "replicas",
            }],
        )
        body = template_of(factory)
        self.assertIn('dig "resource" "status" "availableReplicas" 0 $deploymentObserved', body)
        self.assertIn("{{- if ge (int $deploymentAvailable) (int ($spec.replicas | default 1)) }}", body)

    def test_every_resource_gets_a_unique_name_annotation(self):
        factory = build(resources=[
            {"name": "a", "apiVersion": "v1", "kind": "ConfigMap"},
            {"name": "b", "apiVersion": "v1", "kind": "Secret"},
        ])
        body = template_of(factory)
        self.assertIn('{{ setResourceNameAnnotation "a" }}', body)
        self.assertIn('{{ setResourceNameAnnotation "b" }}', body)
        self.assertEqual(body.count("---"), 2)

    def test_pipeline_always_ends_with_auto_ready(self):
        doc = yaml.safe_load(build().render_composition())
        self.assertEqual(doc["spec"]["pipeline"][-1]["functionRef"]["name"], "function-auto-ready")
        self.assertEqual(doc["spec"]["compositeTypeRef"]["kind"], "XThing")


class TestXRD(unittest.TestCase):
    def test_v2_namespaced_xrd_without_claim_names(self):
        doc = yaml.safe_load(build().render_xrd())
        self.assertEqual(doc["apiVersion"], "apiextensions.crossplane.io/v2")
        self.assertEqual(doc["spec"]["scope"], "Namespaced")
        self.assertNotIn("claimNames", doc["spec"])
        self.assertEqual(doc["metadata"]["name"], "xthings.platform.hooli.tech")

    def test_schema_carries_bounds_enums_and_defaults(self):
        factory = build(fields=[
            {"name": "location", "type": "string", "required": True, "enum": ["EU", "US"]},
            {"name": "size", "type": "integer", "minimum": 1, "maximum": 10, "default": 3},
            {"name": "tags", "type": "object", "additionalProperties": "string"},
        ])
        props = yaml.safe_load(factory.render_xrd())["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
        self.assertEqual(props["properties"]["location"]["enum"], ["EU", "US"])
        self.assertEqual(props["properties"]["size"]["maximum"], 10)
        self.assertEqual(props["properties"]["size"]["default"], 3)
        self.assertEqual(props["properties"]["tags"]["additionalProperties"], {"type": "string"})
        self.assertEqual(props["required"], ["location"])

    def test_printer_column_type_is_inferred_from_the_field(self):
        factory = build(
            fields=[{"name": "size", "type": "integer", "required": True}],
            printerColumns=[{"name": "SIZE", "jsonPath": ".spec.size"}],
        )
        column = yaml.safe_load(factory.render_xrd())["spec"]["versions"][0]["additionalPrinterColumns"][0]
        self.assertEqual(column, {"name": "SIZE", "type": "integer", "jsonPath": ".spec.size"})


class TestKyverno(unittest.TestCase):
    def rules(self, factory):
        return {r["name"]: r for r in yaml.safe_load(factory.render_kyverno_policy())["spec"]["rules"]}

    def test_enum_becomes_an_all_not_in_deny(self):
        factory = build(fields=[
            {"name": "location", "type": "string", "required": True, "enum": ["EU", "US"]},
        ])
        rule = self.rules(factory)["deny-invalid-location"]
        condition = rule["validate"]["deny"]["conditions"]["all"][0]
        self.assertEqual(condition["operator"], "AllNotIn")
        self.assertEqual(condition["value"], ["EU", "US"])
        self.assertNotIn("preconditions", rule)

    def test_bounds_become_greater_than_and_less_than_denies(self):
        factory = build(fields=[
            {"name": "maxMessageSize", "type": "integer", "minimum": 1024, "maximum": 262144},
        ])
        rule = self.rules(factory)["deny-invalid-max-message-size"]
        operators = {c["operator"]: c["value"] for c in rule["validate"]["deny"]["conditions"]["any"]}
        self.assertEqual(operators, {"GreaterThan": 262144, "LessThan": 1024})

    def test_optional_fields_are_gated_on_presence(self):
        factory = build(fields=[{"name": "size", "type": "integer", "minimum": 1}])
        rule = self.rules(factory)["deny-invalid-size"]
        self.assertEqual(rule["preconditions"]["all"][0]["operator"], "NotEquals")

    def test_a_schema_default_does_not_count_as_present(self):
        """CI runs these policies over raw YAML, where nothing has defaulted it.

        Without the precondition, `deny-invalid-size` on an XR that omits the
        field errors with `Unknown key "size" in path` rather than passing.
        """
        factory = build(fields=[{"name": "size", "type": "integer", "minimum": 1, "default": 3}])
        rule = self.rules(factory)["deny-invalid-size"]
        self.assertIn("preconditions", rule)
        # The composition still skips the `{{- with }}` guard for the same field,
        # because there the emitted `| default 3` covers an absent value.
        self.assertIn("size: {{ $spec.size | default 3 }}", template_of(factory))

    def test_required_fields_get_a_presence_rule_that_works_for_numbers(self):
        rule = self.rules(build())["require-size"]
        condition = rule["validate"]["deny"]["conditions"]["all"][0]
        self.assertEqual(condition, {"key": "{{ request.object.spec.size || '' }}",
                                     "operator": "Equals", "value": ""})

    def test_policy_matches_the_group_version_kind_of_the_xr(self):
        rule = self.rules(build())["require-size"]
        self.assertEqual(rule["match"]["resources"]["kinds"],
                         ["platform.hooli.tech/v1alpha1/XThing"])

    def test_rbac_aggregates_into_the_kyverno_controllers(self):
        doc = yaml.safe_load(build().render_kyverno_rbac())
        self.assertEqual(doc["metadata"]["labels"], {
            "rbac.kyverno.io/aggregate-to-reports-controller": "true",
            "rbac.kyverno.io/aggregate-to-background-controller": "true",
        })
        self.assertEqual(doc["rules"][0]["resources"], ["xthings"])

    def test_a_schema_with_no_constraints_produces_no_policy(self):
        factory = build(fields=[{"name": "note", "type": "string"}])
        self.assertIsNone(factory.render_kyverno_policy())


class TestExampleXR(unittest.TestCase):
    def test_example_uses_declared_examples_and_placeholders(self):
        factory = build(fields=[
            {"name": "location", "type": "string", "required": True, "enum": ["EU", "US"]},
            {"name": "providerName", "type": "string", "required": True, "example": "default"},
            {"name": "note", "type": "string"},
        ])
        doc = yaml.safe_load(factory.render_example())
        self.assertEqual(doc["apiVersion"], "platform.hooli.tech/v1alpha1")
        self.assertEqual(doc["metadata"]["namespace"], "team-a")
        self.assertEqual(doc["spec"]["location"], "EU")
        self.assertEqual(doc["spec"]["providerName"], "default")
        # Optional fields are left out rather than guessed at.
        self.assertNotIn("note", doc["spec"])


class TestSelfCheck(unittest.TestCase):
    def test_every_generated_file_is_valid_yaml(self):
        for name in sorted(os.listdir(EXAMPLES)):
            with self.subTest(example=name):
                self.assertEqual(check_output(load_example(name).plan()), [])

    def test_plan_covers_the_expected_paths(self):
        paths = set(load_example("xqueue.yaml").plan())
        self.assertEqual(paths, {
            "crossplane/xrds/xqueue.yaml",
            "crossplane/compositions/aws/xqueue.yaml",
            "kyverno/validate-xqueue-fields.yaml",
            "kyverno/rbac-xqueue.yaml",
            "crossplane/xrs/xqueue-example.yaml",
        })


class TestFidelityToHandWrittenManifests(unittest.TestCase):
    """The examples restate the two manifests already in the repo.

    If the factory stops reproducing them, it has drifted from the idioms the
    stack actually runs -- which is the only reason to trust its output on a
    new API.
    """

    def hand_written(self, path):
        with open(os.path.join(REPO, path), encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def test_xqueue_composition_matches(self):
        generated = yaml.safe_load(load_example("xqueue.yaml").render_composition())
        hand = self.hand_written("crossplane/compositions/aws/xqueue.yaml")
        self.assertEqual(generated["metadata"]["name"], hand["metadata"]["name"])
        self.assertEqual(generated["spec"]["compositeTypeRef"], hand["spec"]["compositeTypeRef"])

        def normalise(doc):
            body = doc["spec"]["pipeline"][0]["input"]["inline"]["template"]
            # The hand-written file explains the ClusterProviderConfig choice in
            # a comment and leaves providerName unquoted; neither changes what
            # the template renders.
            lines = [l for l in body.split("\n") if l.strip() and not l.strip().startswith("#")]
            return "\n".join(lines).replace(" | quote }}", " }}").replace(
                "{{- $xr := .observed.composite.resource -}}\n{{- $spec := $xr.spec -}}",
                "{{- $spec := .observed.composite.resource.spec -}}",
            ).strip()

        self.assertEqual(normalise(generated), normalise(hand))

    def test_xqueue_xrd_is_a_superset_of_the_hand_written_schema(self):
        generated = yaml.safe_load(load_example("xqueue.yaml").render_xrd())
        hand = self.hand_written("crossplane/xrds/xqueue.yaml")
        self.assertEqual(generated["metadata"], hand["metadata"])
        self.assertEqual(generated["spec"]["names"], hand["spec"]["names"])
        self.assertEqual(generated["spec"]["scope"], hand["spec"]["scope"])

        def props(doc):
            return doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]

        gen_props, hand_props = props(generated), props(hand)
        self.assertEqual(gen_props["required"], hand_props["required"])
        for name, schema in hand_props["properties"].items():
            for key, value in schema.items():
                self.assertEqual(gen_props["properties"][name][key], value,
                                 "%s.%s drifted" % (name, key))

    def test_xmicroservice_composition_matches(self):
        generated = yaml.safe_load(load_example("xmicroservice.yaml").render_composition())
        hand = self.hand_written("crossplane/compositions/kubernetes/xmicroservice.yaml")
        self.assertEqual(generated["metadata"]["name"], hand["metadata"]["name"])
        body = generated["spec"]["pipeline"][0]["input"]["inline"]["template"]
        for idiom in [
            '{{ setResourceNameAnnotation "deployment" }}',
            '{{ setResourceNameAnnotation "service" }}',
            'dig "resource" "status" "availableReplicas" 0 $deploymentObserved',
            "app.kubernetes.io/name: {{ $name }}",
        ]:
            self.assertIn(idiom, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
