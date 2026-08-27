#!/usr/bin/env python3
"""XRD + Composition factory for this stack.

Every platform API in `crossplane/` is the same four or five files written the
same way: a Crossplane v2 `CompositeResourceDefinition`, a `Composition` whose
only real content is a `function-go-templating` step followed by
`function-auto-ready`, a pair of Kyverno manifests that re-state the schema
bounds so CI can check them without an API server, and an example XR. Writing
them by hand is mechanical and drifts -- the go-template indentation, the
`{{- with }}` guards around optional fields, the `setResourceNameAnnotation`
call, the `ClusterProviderConfig` reference, the RBAC aggregation labels.

This script generates all of it from one short YAML spec. It emits the same
idioms the hand-written XQueue and XMicroservice use, so generated output sits
next to them without looking foreign.

    python3 tools/factory/crossplane_factory.py new XBucket --group platform.hooli.tech > spec.yaml
    python3 tools/factory/crossplane_factory.py validate spec.yaml
    python3 tools/factory/crossplane_factory.py generate spec.yaml --dry-run
    python3 tools/factory/crossplane_factory.py generate spec.yaml

Only PyYAML is required on top of the standard library.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - dependency hint, not logic
    sys.exit("PyYAML is required: pip install pyyaml")


TOOL_PATH = "tools/factory/crossplane_factory.py"

# Field types the XRD schema (and therefore the generated form in Backstage)
# knows how to render.
SCALAR_TYPES = {"string", "integer", "number", "boolean"}
FIELD_TYPES = SCALAR_TYPES | {"object", "array"}

# Keys that mark a mapping in the spec as an expression rather than a literal.
EXPR_KEYS = {"fromField", "fromXR", "map", "template"}

READY_MODES = {"provider", "always", "replicas"}


class SpecError(Exception):
    """A problem in the user's spec file -- reported without a traceback."""


# ---------------------------------------------------------------------------
# YAML scalar emission
#
# The composition body is a Go template embedded in YAML, so it cannot be
# produced by yaml.dump: template actions must stay unquoted, and the block
# structure ({{- with }} / {{- end }} around an optional key) has no YAML
# equivalent. Everything below emits text line by line instead.
# ---------------------------------------------------------------------------


class RepoDumper(yaml.SafeDumper):
    """Dumps YAML the way the rest of this repo writes it.

    Two deviations from PyYAML's defaults matter here: sequences are indented
    under their key (`versions:` then two spaces then `- name:`), matching every
    hand-written manifest in the stack, and aliases are disabled so a mapping
    reused across rules (the Kyverno `match:` block) is written out each time
    instead of turning into `&id001` / `*id001`.
    """

    def increase_indent(self, flow=False, indentless=False):  # noqa: D102
        return super().increase_indent(flow, False)

    def ignore_aliases(self, data):  # noqa: D102
        return True


def _represent_str(dumper: yaml.Dumper, data: str):
    """Quote with double quotes when quoting is needed at all."""
    if data != "" and _bare_str_is_safe(data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


def _bare_str_is_safe(text: str) -> bool:
    if text.strip() != text or "\n" in text:
        return False
    try:
        return yaml.safe_load(text) == text
    except yaml.YAMLError:
        return False


RepoDumper.add_representer(str, _represent_str)


def dump(doc: Any) -> str:
    return yaml.dump(doc, Dumper=RepoDumper, sort_keys=False, default_flow_style=False, width=100)


def article(word: str) -> str:
    return "an" if word[:1].upper() in "AEIOUX" else "a"


def yaml_scalar(value: Any) -> str:
    """Render a Python scalar as YAML, quoting only when it would change meaning."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    # Round-trip: if the bare form parses back to the same string it is safe.
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = object()
    needs_quotes = parsed != text or text.strip() != text
    if needs_quotes:
        return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')
    return text


def go_literal(value: Any) -> str:
    """Render a value as a Go template literal (for `| default X`, dict keys...)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"%s"' % str(value).replace('"', '\\"')


def camel_to_kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


class Expr:
    """One templated value inside a composed resource.

    `subject` is the thing the value reads from (`$spec.location`). `render`
    turns it into template text; inside a `{{- with }}` block the subject
    becomes `.`, which is why the two are kept apart.
    """

    def __init__(
        self,
        subject: Optional[str],
        wrap: str = "{}",
        optional: bool = False,
        quote: bool = False,
        default: Any = None,
        kind: str = "scalar",
        raw: Optional[str] = None,
    ):
        self.subject = subject
        self.wrap = wrap  # e.g. 'index (dict "EU" "eu-north-1") {}'
        self.optional = optional
        self.quote = quote
        self.default = default
        self.kind = kind  # "scalar" | "stringMap"
        self.raw = raw  # a verbatim `template:` expression

    def body(self, subject: Optional[str] = None) -> str:
        if self.raw is not None:
            return self.raw
        expr = self.wrap.format(subject or self.subject)
        if self.default is not None:
            expr = "%s | default %s" % (expr, go_literal(self.default))
        if self.quote:
            expr = "%s | quote" % expr
        return expr

    def render(self, subject: Optional[str] = None) -> str:
        return "{{ %s }}" % self.body(subject)


def parse_expr(node: Dict[str, Any], fields: Dict[str, Dict[str, Any]], ctx: "Ctx", path: str) -> Expr:
    """Turn one `{fromField: ...}` style mapping into an Expr."""
    used = EXPR_KEYS & set(node)
    if len(used) != 1:
        raise SpecError(
            "%s: an expression takes exactly one of %s, got %s"
            % (path, ", ".join(sorted(EXPR_KEYS)), ", ".join(sorted(used)) or "none")
        )
    known = EXPR_KEYS | {"optional", "quote", "default"}
    unknown = set(node) - known
    if unknown:
        raise SpecError("%s: unknown expression key(s): %s" % (path, ", ".join(sorted(unknown))))

    quote = bool(node.get("quote", False))
    default = node.get("default")

    def schema_default(field_name: str) -> Any:
        """Fall back to the XRD default, so an absent value still renders.

        The API server fills defaults in for anything applied through it, but a
        composition also renders against XRs that skipped defaulting (a
        `crossplane render` dry run, an older stored object), and `key: <no
        value>` in a managed resource is worse than a redundant `| default`.
        """
        return fields.get(field_name, {}).get("default")

    if "template" in node:
        if node.get("optional"):
            raise SpecError("%s: `optional` is meaningless on a raw `template` expression" % path)
        return Expr(None, raw=str(node["template"]).strip(), quote=quote)

    if "fromXR" in node:
        which = str(node["fromXR"])
        mapping = {"name": "$name", "namespace": "$xr.metadata.namespace"}
        if which not in mapping:
            raise SpecError("%s: fromXR takes 'name' or 'namespace', got %r" % (path, which))
        if which == "name":
            ctx.uses_name = True
        else:
            ctx.uses_namespace = True
        return Expr(mapping[which], quote=quote, default=default)

    if "map" in node:
        spec = node["map"]
        if not isinstance(spec, dict) or "field" not in spec or "values" not in spec:
            raise SpecError("%s: map takes {field: <name>, values: {<in>: <out>}}" % path)
        field = str(spec["field"])
        _require_field(field, fields, path)
        pairs = " ".join(
            "%s %s" % (go_literal(k), go_literal(v)) for k, v in spec["values"].items()
        )
        expr = Expr(
            "$spec.%s" % field,
            wrap="index (dict %s) {}" % pairs,
            optional=_infer_optional(node, field, fields),
            quote=quote,
            default=default,
        )
        return expr

    field = str(node["fromField"])
    declared = _require_field(field, fields, path)
    kind = "stringMap" if _is_string_map(declared) else "scalar"
    # String values are quoted by default: an unquoted image tag, ARN or name
    # can parse as a YAML number, bool or timestamp once rendered. `quote:
    # false` opts out where the target field is genuinely not a string.
    if "quote" in node:
        quote = bool(node["quote"])
    else:
        quote = declared.get("type") == "string"
    optional = _infer_optional(node, field, fields)
    if default is None and not optional:
        default = schema_default(field)
    return Expr(
        "$spec.%s" % field,
        optional=optional,
        quote=quote,
        default=default,
        kind=kind,
    )


def _require_field(name: str, fields: Dict[str, Dict[str, Any]], path: str) -> Dict[str, Any]:
    if name not in fields:
        raise SpecError(
            "%s: references spec field %r, which is not declared under `fields` (declared: %s)"
            % (path, name, ", ".join(sorted(fields)) or "none")
        )
    return fields[name]


def _is_string_map(field: Dict[str, Any]) -> bool:
    return field.get("type") == "object" and field.get("additionalProperties") == "string"


def _infer_optional(node: Dict[str, Any], field: str, fields: Dict[str, Dict[str, Any]]) -> bool:
    """Guard a field with `{{- with }}` unless it is guaranteed to be present.

    A field that is required, or carries a schema default, always has a value,
    so wrapping it would only add noise. Anything else may be absent, and
    emitting `key: {{ $spec.foo }}` for an absent field renders `key: <no
    value>` into the managed resource. An explicit `optional:` in the spec
    always wins.
    """
    if "optional" in node:
        return bool(node["optional"])
    declared = fields[field]
    return not (declared.get("required") or "default" in declared)


class Ctx:
    """Tracks which template preamble variables the body actually referenced."""

    def __init__(self) -> None:
        self.uses_name = False
        self.uses_namespace = False

    @property
    def uses_xr(self) -> bool:
        return self.uses_name or self.uses_namespace


# ---------------------------------------------------------------------------
# Template body emission
# ---------------------------------------------------------------------------


def is_expr_node(value: Any) -> bool:
    return isinstance(value, dict) and bool(EXPR_KEYS & set(value))


def emit_value(
    key: str,
    value: Any,
    indent: int,
    fields: Dict[str, Dict[str, Any]],
    ctx: Ctx,
    path: str,
) -> List[str]:
    """Emit `key: value` at `indent` spaces, recursing into nested structures."""
    pad = " " * indent
    if is_expr_node(value):
        expr = parse_expr(value, fields, ctx, path)
        return emit_expr(key, expr, indent)
    if isinstance(value, dict):
        lines = ["%s%s:" % (pad, key)]
        lines.extend(emit_mapping(value, indent + 2, fields, ctx, path))
        return lines
    if isinstance(value, list):
        lines = ["%s%s:" % (pad, key)]
        lines.extend(emit_list(value, indent + 2, fields, ctx, path))
        return lines
    return ["%s%s: %s" % (pad, key, yaml_scalar(value))]


def emit_expr(key: str, expr: Expr, indent: int) -> List[str]:
    pad = " " * indent
    if expr.kind == "stringMap":
        # The map itself is optional-guarded, then ranged over. Values are
        # quoted because provider tag values are strings even when they look
        # like numbers.
        body = [
            "%s{{- with %s }}" % (pad, expr.subject),
            "%s%s:" % (pad, key),
            "%s  {{- range $key, $value := . }}" % pad,
            "%s  {{ $key }}: {{ $value | quote }}" % pad,
            "%s  {{- end }}" % pad,
            "%s{{- end }}" % pad,
        ]
        if not expr.optional:
            # A required map still renders as a range, just without the guard.
            body = body[1:-1]
        return body
    if expr.optional:
        return [
            "%s{{- with %s }}" % (pad, expr.subject),
            "%s%s: %s" % (pad, key, expr.render(".")),
            "%s{{- end }}" % pad,
        ]
    return ["%s%s: %s" % (pad, key, expr.render())]


def emit_mapping(
    node: Dict[str, Any],
    indent: int,
    fields: Dict[str, Dict[str, Any]],
    ctx: Ctx,
    path: str,
) -> List[str]:
    lines: List[str] = []
    for key, value in node.items():
        lines.extend(emit_value(str(key), value, indent, fields, ctx, "%s.%s" % (path, key)))
    return lines


def emit_list(
    items: Sequence[Any],
    indent: int,
    fields: Dict[str, Dict[str, Any]],
    ctx: Ctx,
    path: str,
) -> List[str]:
    pad = " " * indent
    lines: List[str] = []
    for i, item in enumerate(items):
        item_path = "%s[%d]" % (path, i)
        if is_expr_node(item):
            expr = parse_expr(item, fields, ctx, item_path)
            lines.append("%s- %s" % (pad, expr.render()))
        elif isinstance(item, dict):
            block = emit_mapping(item, indent + 2, fields, ctx, item_path)
            first = block[0].lstrip() if block else ""
            lines.append("%s- %s" % (pad, first))
            lines.extend(block[1:])
        elif isinstance(item, list):
            lines.append("%s-" % pad)
            lines.extend(emit_list(item, indent + 2, fields, ctx, item_path))
        else:
            lines.append("%s- %s" % (pad, yaml_scalar(item)))
    return lines


# ---------------------------------------------------------------------------
# Spec parsing / validation
# ---------------------------------------------------------------------------


class Factory:
    def __init__(self, spec: Dict[str, Any], source: str):
        self.source = source
        self.raw = spec
        self._parse()

    # -- parsing -----------------------------------------------------------

    def _parse(self) -> None:
        spec = self.raw
        if not isinstance(spec, dict):
            raise SpecError("the spec file must contain a single YAML mapping")

        allowed = {
            "apiVersion", "kind", "group", "version", "plural", "singular", "scope",
            "provider", "compositionName", "description", "fields", "printerColumns",
            "resources", "kyverno", "example",
        }
        unknown = set(spec) - allowed
        if unknown:
            raise SpecError("unknown top-level key(s): %s" % ", ".join(sorted(unknown)))

        self.kind = self._required(spec, "kind")
        if not re.match(r"^[A-Z][A-Za-z0-9]*$", self.kind):
            raise SpecError("kind must be UpperCamelCase, got %r" % self.kind)
        self.group = self._required(spec, "group")
        if "." not in self.group:
            raise SpecError("group must be a DNS name such as platform.hooli.tech, got %r" % self.group)
        self.version = spec.get("version", "v1alpha1")
        self.singular = spec.get("singular", self.kind.lower())
        self.plural = spec.get("plural", self.singular + "s")
        self.scope = spec.get("scope", "Namespaced")
        if self.scope not in {"Namespaced", "Cluster"}:
            raise SpecError("scope must be Namespaced or Cluster, got %r" % self.scope)
        self.provider = spec.get("provider", "kubernetes")
        self.description = spec.get("description")
        self.composition_name = spec.get(
            "compositionName", "%s.%s.%s" % (self.plural, self.provider, self.group)
        )

        self.fields = self._parse_fields(spec.get("fields") or [])
        self.printer_columns = self._parse_printer_columns(spec.get("printerColumns"))
        self.resources = self._parse_resources(spec.get("resources") or [])
        self.kyverno = self._parse_kyverno(spec.get("kyverno"))
        self.example = spec.get("example") or {}
        if not isinstance(self.example, dict):
            raise SpecError("`example` must be a mapping")

        # Expressions inside `resources` are only checked while the template is
        # emitted -- a typo'd `fromField` would otherwise surface at `generate`
        # time and not at `validate` time. Render once and throw the result
        # away so every error is a construction error.
        self.render_composition()

    @staticmethod
    def _required(spec: Dict[str, Any], key: str) -> str:
        if key not in spec or spec[key] in (None, ""):
            raise SpecError("missing required top-level key: %s" % key)
        return str(spec[key])

    def _parse_fields(self, fields: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(fields, list) or not fields:
            raise SpecError("`fields` must be a non-empty list")
        allowed = {
            "name", "type", "description", "required", "default", "enum",
            "minimum", "maximum", "pattern", "additionalProperties", "items", "example",
        }
        out: Dict[str, Dict[str, Any]] = {}
        for i, field in enumerate(fields):
            if not isinstance(field, dict) or "name" not in field:
                raise SpecError("fields[%d]: each field needs at least a `name`" % i)
            name = str(field["name"])
            if name in out:
                raise SpecError("fields[%d]: duplicate field %r" % (i, name))
            unknown = set(field) - allowed
            if unknown:
                raise SpecError("fields[%s]: unknown key(s): %s" % (name, ", ".join(sorted(unknown))))
            ftype = field.get("type", "string")
            if ftype not in FIELD_TYPES:
                raise SpecError(
                    "fields[%s]: type must be one of %s, got %r"
                    % (name, ", ".join(sorted(FIELD_TYPES)), ftype)
                )
            if field.get("enum") is not None and not isinstance(field["enum"], list):
                raise SpecError("fields[%s]: enum must be a list" % name)
            if ftype == "array" and "items" not in field:
                raise SpecError("fields[%s]: an array field needs `items`" % name)
            for bound in ("minimum", "maximum"):
                if bound in field and ftype not in {"integer", "number"}:
                    raise SpecError("fields[%s]: %s only applies to numeric fields" % (name, bound))
            field = dict(field, type=ftype)
            out[name] = field
        return out

    def _parse_printer_columns(self, columns: Any) -> List[Dict[str, Any]]:
        if columns is None:
            # Default to the first required field, mirroring the XQueue XRD --
            # Crossplane already appends SYNCED/READY/COMPOSITION/AGE, so
            # repeating those only duplicates columns.
            first = next((f for f in self.fields.values() if f.get("required")), None)
            if first is None:
                return []
            columns = [{"name": str(first["name"]).upper(), "jsonPath": ".spec.%s" % first["name"]}]
        if not isinstance(columns, list):
            raise SpecError("`printerColumns` must be a list")
        out = []
        for i, col in enumerate(columns):
            if not isinstance(col, dict) or "jsonPath" not in col:
                raise SpecError("printerColumns[%d]: needs at least a `jsonPath`" % i)
            json_path = str(col["jsonPath"])
            name = str(col.get("name") or json_path.rsplit(".", 1)[-1].upper())
            ctype = col.get("type")
            if ctype is None:
                referenced = json_path[len(".spec."):] if json_path.startswith(".spec.") else None
                declared = self.fields.get(referenced or "", {})
                ctype = declared.get("type", "string")
                if ctype not in SCALAR_TYPES:
                    ctype = "string"
            out.append({"name": name, "type": ctype, "jsonPath": json_path})
        return out

    def _parse_resources(self, resources: Any) -> List[Dict[str, Any]]:
        if not isinstance(resources, list) or not resources:
            raise SpecError("`resources` must be a non-empty list -- a composition with no composed resource does nothing")
        allowed = {
            "name", "apiVersion", "kind", "metadataName", "namespace", "labels",
            "annotations", "ready", "replicasField", "providerConfigRef", "spec", "data", "stringData",
        }
        seen = set()
        out = []
        for i, res in enumerate(resources):
            if not isinstance(res, dict):
                raise SpecError("resources[%d]: must be a mapping" % i)
            unknown = set(res) - allowed
            if unknown:
                raise SpecError("resources[%d]: unknown key(s): %s" % (i, ", ".join(sorted(unknown))))
            for key in ("name", "apiVersion", "kind"):
                if not res.get(key):
                    raise SpecError("resources[%d]: missing required key %s" % (i, key))
            name = str(res["name"])
            if name in seen:
                raise SpecError("resources[%d]: duplicate resource name %r -- setResourceNameAnnotation must be unique" % (i, name))
            seen.add(name)
            ready = res.get("ready", "provider")
            if ready not in READY_MODES:
                raise SpecError(
                    "resources[%s]: ready must be one of %s, got %r"
                    % (name, ", ".join(sorted(READY_MODES)), ready)
                )
            if ready == "replicas":
                replicas_field = res.get("replicasField", "replicas")
                if replicas_field not in self.fields:
                    raise SpecError(
                        "resources[%s]: ready: replicas needs a spec field %r to compare against"
                        % (name, replicas_field)
                    )
            if self.scope == "Namespaced" and res.get("namespace"):
                raise SpecError(
                    "resources[%s]: drop `namespace` -- Crossplane places composed resources in the XR's own namespace"
                    % name
                )
            out.append(dict(res, ready=ready))
        return out

    def _parse_kyverno(self, kyverno: Any) -> Dict[str, Any]:
        if kyverno is None:
            kyverno = {}
        if not isinstance(kyverno, dict):
            raise SpecError("`kyverno` must be a mapping")
        unknown = set(kyverno) - {"enabled", "validationFailureAction", "background"}
        if unknown:
            raise SpecError("kyverno: unknown key(s): %s" % ", ".join(sorted(unknown)))
        return {
            "enabled": kyverno.get("enabled", True),
            "validationFailureAction": kyverno.get("validationFailureAction", "Enforce"),
            "background": kyverno.get("background", True),
        }

    # -- shared bits -------------------------------------------------------

    @property
    def api_version(self) -> str:
        return "%s/%s" % (self.group, self.version)

    def header(self, purpose: str) -> str:
        lines = [
            "# %s" % purpose,
            "#",
            "# Generated by %s from %s." % (TOOL_PATH, self.source),
            "# Edit the spec and regenerate rather than editing this file by hand:",
            "#   python3 %s generate %s --force" % (TOOL_PATH, self.source),
        ]
        if self.description:
            lines.append("#")
            for line in textwrap.wrap(self.description, 74):
                lines.append("# %s" % line)
        return "\n".join(lines) + "\n"

    # -- XRD ---------------------------------------------------------------

    def render_xrd(self) -> str:
        props: Dict[str, Any] = {}
        required: List[str] = []
        for name, field in self.fields.items():
            prop: Dict[str, Any] = {"type": field["type"]}
            if field.get("description"):
                prop["description"] = field["description"]
            if field.get("enum") is not None:
                prop["enum"] = list(field["enum"])
            for key in ("minimum", "maximum", "pattern"):
                if key in field:
                    prop[key] = field[key]
            if field["type"] == "object" and "additionalProperties" in field:
                ap = field["additionalProperties"]
                prop["additionalProperties"] = {"type": ap} if isinstance(ap, str) else ap
            if field["type"] == "array":
                items = field["items"]
                prop["items"] = {"type": items} if isinstance(items, str) else items
            if "default" in field:
                prop["default"] = field["default"]
            props[name] = prop
            if field.get("required"):
                required.append(name)

        spec_schema: Dict[str, Any] = {"type": "object", "properties": props}
        if required:
            spec_schema["required"] = required

        version: Dict[str, Any] = {
            "name": self.version,
            "served": True,
            "referenceable": True,
            "schema": {
                "openAPIV3Schema": {
                    "type": "object",
                    "properties": {"spec": spec_schema},
                }
            },
        }
        if self.printer_columns:
            version["additionalPrinterColumns"] = self.printer_columns

        doc = {
            "apiVersion": "apiextensions.crossplane.io/v2",
            "kind": "CompositeResourceDefinition",
            "metadata": {"name": "%s.%s" % (self.plural, self.group)},
            "spec": {
                "group": self.group,
                "scope": self.scope,
                "names": {"kind": self.kind, "plural": self.plural},
                "versions": [version],
            },
        }
        body = dump(doc)
        note = (
            "#\n"
            "# Crossplane v2: `scope: Namespaced` makes the XR itself the user-facing API.\n"
            "# Claims are gone -- `claimNames` is deprecated and does nothing on an\n"
            "# apiextensions.crossplane.io/v2 XRD -- so a team creates %s %s directly in\n"
            "# its own namespace.\n" % (article(self.kind), self.kind)
            if self.scope == "Namespaced"
            else ""
        )
        return self.header("Composite resource definition for %s -- the platform API." % self.kind) + note + body

    # -- Composition -------------------------------------------------------

    def render_composition(self) -> str:
        ctx = Ctx()
        blocks: List[List[str]] = []
        preludes: List[str] = []

        for res in self.resources:
            lines, prelude = self._render_resource(res, ctx)
            blocks.append(lines)
            preludes.extend(prelude)

        # Only bind what the body reads. A composition that never touches the
        # XR's own metadata does not need $xr at all.
        if ctx.uses_xr:
            template_lines: List[str] = [
                "{{- $xr := .observed.composite.resource -}}",
                "{{- $spec := $xr.spec -}}",
            ]
            if ctx.uses_name:
                template_lines.append("{{- $name := $xr.metadata.name -}}")
        else:
            template_lines = ["{{- $spec := .observed.composite.resource.spec -}}"]
        if preludes:
            template_lines.append("")
            template_lines.extend(preludes)

        multi = len(blocks) > 1
        for block in blocks:
            template_lines.append("")
            if multi:
                template_lines.append("---")
            template_lines.extend(block)

        template = "\n".join(template_lines).rstrip() + "\n"

        out = [
            self.header("Composition for %s, rendered with function-go-templating." % self.kind),
            "apiVersion: apiextensions.crossplane.io/v1",
            "kind: Composition",
            "metadata:",
            "  name: %s" % self.composition_name,
            "spec:",
            "  compositeTypeRef:",
            "    apiVersion: %s" % self.api_version,
            "    kind: %s" % self.kind,
            "  mode: Pipeline",
            "  pipeline:",
            "    - step: render-resources",
            "      functionRef:",
            "        name: function-go-templating",
            "      input:",
            "        apiVersion: gotemplating.fn.crossplane.io/v1beta1",
            "        kind: GoTemplate",
            "        source: Inline",
            "        inline:",
            "          template: |",
        ]
        out.extend(textwrap.indent(template.rstrip("\n"), " " * 12).split("\n"))
        out.extend([
            "",
            "    # go-templating does not derive XR readiness on its own. auto-ready marks",
            "    # the %s Ready once every composed resource reports Ready." % self.kind,
            "    - step: auto-ready",
            "      functionRef:",
            "        name: function-auto-ready",
        ])
        return "\n".join(out) + "\n"

    def _render_resource(self, res: Dict[str, Any], ctx: Ctx) -> Tuple[List[str], List[str]]:
        name = str(res["name"])
        prelude: List[str] = []
        ready_annotation: List[str] = []

        if res["ready"] == "always":
            # Nothing further to wait on -- a Service or ConfigMap is functional
            # the moment it exists.
            ready_annotation = ['    gotemplating.fn.crossplane.io/ready: "True"']
        elif res["ready"] == "replicas":
            # A Deployment reports Available, not the Ready condition
            # function-auto-ready looks for, so it would never count as ready and
            # the XR would sit at Creating forever. Derive readiness from the
            # observed replica count instead of asserting ready unconditionally.
            field = res.get("replicasField", "replicas")
            default = self.fields[field].get("default", 1)
            var = re.sub(r"[^a-zA-Z0-9]", "", name)
            prelude.extend([
                "{{- $observed := .observed.resources | default dict -}}",
                '{{- $%sObserved := index $observed "%s" -}}' % (var, name),
                "{{- $%sAvailable := 0 -}}" % var,
                "{{- if $%sObserved -}}" % var,
                '{{-   $%sAvailable = dig "resource" "status" "availableReplicas" 0 $%sObserved -}}' % (var, var),
                "{{- end -}}",
            ])
            ready_annotation = [
                "    {{- if ge (int $%sAvailable) (int ($spec.%s | default %s)) }}"
                % (var, field, go_literal(default)),
                '    gotemplating.fn.crossplane.io/ready: "True"',
                "    {{- end }}",
            ]

        lines = [
            "apiVersion: %s" % res["apiVersion"],
            "kind: %s" % res["kind"],
            "metadata:",
        ]

        meta_name = res.get("metadataName", {"fromXR": "name"})
        if meta_name is not False:
            if is_expr_node(meta_name):
                expr = parse_expr(meta_name, self.fields, ctx, "resources[%s].metadataName" % name)
                lines.append("  name: %s" % expr.render())
            else:
                lines.append("  name: %s" % yaml_scalar(meta_name))

        if res.get("labels"):
            lines.append("  labels:")
            lines.extend(emit_mapping(res["labels"], 4, self.fields, ctx, "resources[%s].labels" % name))

        lines.append("  annotations:")
        lines.append('    {{ setResourceNameAnnotation "%s" }}' % name)
        if res.get("annotations"):
            lines.extend(emit_mapping(res["annotations"], 4, self.fields, ctx, "resources[%s].annotations" % name))
        lines.extend(ready_annotation)

        body = dict(res.get("spec") or {})
        if res.get("providerConfigRef"):
            body["providerConfigRef"] = self._provider_config_ref(res["providerConfigRef"], name)
        for extra in ("data", "stringData"):
            if res.get(extra):
                lines.append("%s:" % extra)
                lines.extend(emit_mapping(res[extra], 2, self.fields, ctx, "resources[%s].%s" % (name, extra)))
        if body:
            lines.append("spec:")
            lines.extend(emit_mapping(body, 2, self.fields, ctx, "resources[%s].spec" % name))

        return lines, prelude

    def _provider_config_ref(self, ref: Any, res_name: str) -> Dict[str, Any]:
        if not isinstance(ref, dict):
            raise SpecError("resources[%s].providerConfigRef must be a mapping" % res_name)
        unknown = set(ref) - {"kind", "name", "fromField"}
        if unknown:
            raise SpecError(
                "resources[%s].providerConfigRef: unknown key(s): %s" % (res_name, ", ".join(sorted(unknown)))
            )
        # Namespaced managed resources may reference either a namespaced
        # ProviderConfig or a shared ClusterProviderConfig. This stack uses one
        # cluster-scoped config so every tenant namespace shares the LocalStack
        # endpoint without a per-namespace copy.
        out: Dict[str, Any] = {"kind": ref.get("kind", "ClusterProviderConfig")}
        if "fromField" in ref:
            out["name"] = {"fromField": ref["fromField"], "optional": False}
        else:
            out["name"] = ref.get("name", "default")
        return out

    # -- Kyverno -----------------------------------------------------------

    def render_kyverno_policy(self) -> Optional[str]:
        rules: List[Dict[str, Any]] = []

        def match() -> Dict[str, Any]:
            return {"resources": {"kinds": ["%s/%s/%s" % (self.group, self.version, self.kind)]}}

        for name, field in self.fields.items():
            key = "{{ request.object.spec.%s }}" % name
            # Only `required` guarantees a value here. A schema `default` does
            # not: these policies also run in CI over the raw YAML in
            # crossplane/xrs, where no API server has defaulted anything, and a
            # rule that dereferences an absent key errors out instead of
            # passing. (The composition's `{{- with }}` guard uses a different
            # rule -- there a default is enough, because the template emits
            # `| default <value>` alongside it.)
            may_be_absent = not field.get("required")
            precondition = (
                {"all": [{"key": "{{ request.object.spec.%s || '' }}" % name,
                          "operator": "NotEquals", "value": ""}]}
                if may_be_absent else None
            )

            if field.get("enum"):
                allowed = [str(v) for v in field["enum"]]
                rule: Dict[str, Any] = {
                    "name": "deny-invalid-%s" % camel_to_kebab(name),
                    "match": match(),
                }
                if precondition:
                    rule["preconditions"] = precondition
                rule["validate"] = {
                    "message": "Invalid %s: only %s are allowed in spec.%s"
                    % (name, " or ".join("'%s'" % v for v in allowed), name),
                    "deny": {"conditions": {"all": [
                        {"key": key, "operator": "AllNotIn", "value": allowed}
                    ]}},
                }
                rules.append(rule)

            conditions = []
            if "maximum" in field:
                conditions.append({"key": key, "operator": "GreaterThan", "value": field["maximum"]})
            if "minimum" in field:
                conditions.append({"key": key, "operator": "LessThan", "value": field["minimum"]})
            if conditions:
                if "minimum" in field and "maximum" in field:
                    message = "Invalid %s: must be between %s and %s" % (name, field["minimum"], field["maximum"])
                elif "minimum" in field:
                    message = "Invalid %s: must be at least %s" % (name, field["minimum"])
                else:
                    message = "Invalid %s: must be at most %s" % (name, field["maximum"])
                rule = {"name": "deny-invalid-%s" % camel_to_kebab(name), "match": match()}
                if any(r["name"] == rule["name"] for r in rules):
                    rule["name"] = "deny-out-of-range-%s" % camel_to_kebab(name)
                if precondition:
                    rule["preconditions"] = precondition
                rule["validate"] = {
                    "message": message,
                    "deny": {"conditions": {"any": conditions}},
                }
                rules.append(rule)

            if field.get("required"):
                # A deny on absence rather than a `?*` pattern anchor: the
                # anchor is a string matcher, so it does the wrong thing on the
                # integer and boolean fields an XRD is just as likely to require.
                rules.append({
                    "name": "require-%s" % camel_to_kebab(name),
                    "match": match(),
                    "validate": {
                        "message": "spec.%s is required on %s %s" % (name, article(self.kind), self.kind),
                        "deny": {"conditions": {"all": [
                            {"key": "{{ request.object.spec.%s || '' }}" % name,
                             "operator": "Equals", "value": ""}
                        ]}},
                    },
                })

        if not rules:
            return None

        doc = {
            "apiVersion": "kyverno.io/v1",
            "kind": "ClusterPolicy",
            "metadata": {"name": "validate-%s-fields" % self.singular},
            "spec": {
                "validationFailureAction": self.kyverno["validationFailureAction"],
                "background": self.kyverno["background"],
                "rules": rules,
            },
        }
        note = (
            "#\n"
            "# These rules duplicate the bounds in the XRD's openAPIV3Schema on purpose:\n"
            "# the same policies run in CI against the YAML in crossplane/xrs before a PR\n"
            "# merges, where no API server is available to enforce the schema.\n"
        )
        return (
            self.header("Kyverno validation for the %s platform API." % self.kind)
            + note
            + dump(doc)
        )

    def render_kyverno_rbac(self) -> str:
        doc = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {
                "name": "kyverno:%s-%s" % (self.group.replace(".", "-"), self.plural),
                "labels": {
                    "rbac.kyverno.io/aggregate-to-reports-controller": "true",
                    "rbac.kyverno.io/aggregate-to-background-controller": "true",
                },
            },
            "rules": [{
                "apiGroups": [self.group],
                "resources": [self.plural],
                "verbs": ["get", "list", "watch"],
            }],
        }
        note = (
            "#\n"
            "# Kyverno's controllers only get RBAC for built-in resources out of the box.\n"
            "# Admission validation works without this (the webhook sees the request\n"
            "# payload), but the policy declares `background: true`, and background\n"
            "# scanning needs to read existing %ss. These labels aggregate the rule into\n"
            "# the controller roles Kyverno assembles at runtime.\n" % self.kind
        )
        return (
            self.header("RBAC so Kyverno can background-scan %s resources." % self.kind)
            + note
            + dump(doc)
        )

    # -- Example XR --------------------------------------------------------

    def example_name(self) -> str:
        return str(self.example.get("name") or "%s-example" % self.singular)

    def render_example(self) -> str:
        values = dict(self.example.get("spec") or {})
        for name, field in self.fields.items():
            if name in values:
                continue
            if "example" in field:
                values[name] = field["example"]
            elif field.get("required"):
                values[name] = self._placeholder(field)
        doc: Dict[str, Any] = {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {"name": self.example_name()},
        }
        if self.scope == "Namespaced":
            doc["metadata"]["namespace"] = str(self.example.get("namespace") or "team-a")
        doc["spec"] = values
        return (
            self.header("Example %s. Copy it, rename it and commit it under crossplane/xrs/." % self.kind)
            + dump(doc)
        )

    @staticmethod
    def _placeholder(field: Dict[str, Any]) -> Any:
        if field.get("enum"):
            return field["enum"][0]
        ftype = field["type"]
        if ftype in {"integer", "number"}:
            return field.get("minimum", 1)
        if ftype == "boolean":
            return False
        if ftype == "object":
            return {}
        if ftype == "array":
            return []
        return "CHANGEME"

    # -- output plan -------------------------------------------------------

    def plan(self, with_kyverno: bool = True, with_example: bool = True) -> Dict[str, str]:
        files = {
            "crossplane/xrds/%s.yaml" % self.singular: self.render_xrd(),
            "crossplane/compositions/%s/%s.yaml" % (self.provider, self.singular): self.render_composition(),
        }
        if with_kyverno and self.kyverno["enabled"]:
            policy = self.render_kyverno_policy()
            if policy:
                files["kyverno/validate-%s-fields.yaml" % self.singular] = policy
                files["kyverno/rbac-%s.yaml" % self.singular] = self.render_kyverno_rbac()
        if with_example:
            files["crossplane/xrs/%s.yaml" % self.example_name()] = self.render_example()
        return files


# ---------------------------------------------------------------------------
# Self-check: every generated file must be parseable YAML
# ---------------------------------------------------------------------------


def check_output(files: Dict[str, str]) -> List[str]:
    problems = []
    for path, content in files.items():
        try:
            docs = list(yaml.safe_load_all(content))
        except yaml.YAMLError as exc:
            problems.append("%s: generated file is not valid YAML: %s" % (path, exc))
            continue
        if not docs or docs[0] is None:
            problems.append("%s: generated file is empty" % path)
            continue
        if "compositions/" in path:
            problems.extend(check_composition(path, docs[0]))
    return problems


def check_composition(path: str, doc: Any) -> List[str]:
    """The composition body is a string to YAML; check it is sane Go template."""
    problems = []
    try:
        template = doc["spec"]["pipeline"][0]["input"]["inline"]["template"]
    except (KeyError, IndexError, TypeError):
        return ["%s: composition has no go-templating step" % path]
    opens = template.count("{{")
    closes = template.count("}}")
    if opens != closes:
        problems.append("%s: unbalanced template braces (%d '{{' vs %d '}}')" % (path, opens, closes))
    starts = len(re.findall(r"\{\{-?\s*(?:if|with|range)\b", template))
    ends = len(re.findall(r"\{\{-?\s*end\b", template))
    if starts != ends:
        problems.append("%s: %d if/with/range blocks but %d end actions" % (path, starts, ends))
    # Strip the template actions and confirm what is left is still YAML-shaped.
    stripped = re.sub(r"\{\{-?.*?-?\}\}", "placeholder", template, flags=re.S)
    stripped = "\n".join(l for l in stripped.split("\n") if l.strip() != "placeholder")
    try:
        list(yaml.safe_load_all(stripped))
    except yaml.YAMLError as exc:
        problems.append("%s: rendered template is not YAML-shaped: %s" % (path, exc))
    return problems


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def load_spec(path: str) -> Factory:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return Factory(data, os.path.relpath(path))


def cmd_generate(args: argparse.Namespace) -> int:
    factory = load_spec(args.spec)
    files = factory.plan(with_kyverno=not args.no_kyverno, with_example=not args.no_example)

    problems = check_output(files)
    if problems:
        for problem in problems:
            print("error: %s" % problem, file=sys.stderr)
        return 1

    if args.dry_run:
        for path in sorted(files):
            print("# ---------- %s ----------" % path)
            print(files[path])
        return 0

    existing = [p for p in files if os.path.exists(os.path.join(args.root, p))]
    if existing and not args.force:
        print("error: refusing to overwrite existing file(s); pass --force:", file=sys.stderr)
        for path in sorted(existing):
            print("  %s" % path, file=sys.stderr)
        return 1

    for path in sorted(files):
        target = os.path.join(args.root, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(files[path])
        print("wrote %s" % path)

    print()
    print("Next:")
    print("  kyverno apply ./kyverno --resource ./crossplane/xrs")
    print("  git add crossplane kyverno && git commit -m 'feat(xrd): add %s'" % factory.kind)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    factory = load_spec(args.spec)
    problems = check_output(factory.plan())
    if problems:
        for problem in problems:
            print("error: %s" % problem, file=sys.stderr)
        return 1
    print("%s: %s/%s is valid (%d field(s), %d composed resource(s))" % (
        args.spec, factory.group, factory.kind, len(factory.fields), len(factory.resources)))
    return 0


SCAFFOLD = """\
# Spec for {kind}, consumed by {tool}.
kind: {kind}
group: {group}
version: v1alpha1
provider: {provider}
description: >-
  One sentence on what this platform API gives a team.

fields:
  - name: location
    type: string
    description: Where the resource lives.
    required: true
    enum:
      - EU
      - US
  - name: sizeGb
    type: integer
    description: Requested capacity.
    minimum: 1
    maximum: 1024
    default: 10
  - name: tags
    type: object
    description: Free-form tags copied onto the provider resource.
    additionalProperties: string

resources:
  - name: main
    apiVersion: example.aws.m.upbound.io/v1beta1
    kind: Example
    # provider | always | replicas -- see tools/factory/README.md.
    ready: provider
    providerConfigRef:
      kind: ClusterProviderConfig
      name: default
    spec:
      forProvider:
        # A literal passes through; `map` picks a value per input; `fromField`
        # reads a spec field and is guarded with {{{{- with }}}} when optional.
        region:
          map:
            field: location
            values:
              EU: eu-north-1
              US: us-east-2
        sizeGb:
          fromField: sizeGb
        tags:
          fromField: tags
"""


def cmd_new(args: argparse.Namespace) -> int:
    print(SCAFFOLD.format(kind=args.kind, group=args.group, provider=args.provider, tool=TOOL_PATH), end="")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="crossplane_factory.py",
        description="Generate Crossplane XRDs, go-templating Compositions, Kyverno policies and example XRs from one spec.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              %(prog)s new XBucket --group platform.hooli.tech > tools/factory/examples/xbucket.yaml
              %(prog)s validate tools/factory/examples/xbucket.yaml
              %(prog)s generate tools/factory/examples/xbucket.yaml --dry-run
              %(prog)s generate tools/factory/examples/xbucket.yaml --force
            """
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    default_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    gen = sub.add_parser("generate", help="write the XRD, Composition, Kyverno policy and example XR")
    gen.add_argument("spec", help="path to the factory spec YAML")
    gen.add_argument("--root", default=default_root, help="repository root (default: this checkout)")
    gen.add_argument("--dry-run", action="store_true", help="print to stdout instead of writing files")
    gen.add_argument("--force", action="store_true", help="overwrite existing files")
    gen.add_argument("--no-kyverno", action="store_true", help="skip the Kyverno policy and RBAC")
    gen.add_argument("--no-example", action="store_true", help="skip the example XR")
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate", help="parse the spec and self-check the generated YAML")
    val.add_argument("spec", help="path to the factory spec YAML")
    val.set_defaults(func=cmd_validate)

    new = sub.add_parser("new", help="print a commented starter spec")
    new.add_argument("kind", help="XR kind, UpperCamelCase (e.g. XBucket)")
    new.add_argument("--group", default="platform.hooli.tech", help="API group (default: %(default)s)")
    new.add_argument("--provider", default="aws", help="compositions/<provider>/ subdirectory (default: %(default)s)")
    new.set_defaults(func=cmd_new)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SpecError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except yaml.YAMLError as exc:
        print("error: could not parse spec: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
