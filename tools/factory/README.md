# XRD + Composition factory

Every platform API in this repo is the same set of files, written the same way:

| File | What it is |
| --- | --- |
| `crossplane/xrds/<kind>.yaml` | Crossplane v2 `CompositeResourceDefinition` -- the API teams call |
| `crossplane/compositions/<provider>/<kind>.yaml` | `Composition`: one `function-go-templating` step, then `function-auto-ready` |
| `kyverno/validate-<kind>-fields.yaml` | The schema bounds restated so CI can check XRs before merge |
| `kyverno/rbac-<kind>.yaml` | RBAC so Kyverno's background scanner can read the new kind |
| `crossplane/xrs/<name>.yaml` | An example XR a team can copy |

Writing them by hand is mechanical and drifts. The details that are easy to get
wrong are exactly the ones that fail quietly: a `{{- with }}` guard missing from
an optional field so the managed resource gets `key: <no value>`; a composed
resource in `sqs.aws.upbound.io` instead of `sqs.aws.m.upbound.io`, which a
namespaced XR cannot compose at all; a Deployment that never reports the `Ready`
condition `function-auto-ready` looks for, leaving the XR at `Creating` forever.

`crossplane_factory.py` generates all five files from one short spec, using the
idioms the hand-written `XQueue` and `XMicroservice` already use. It needs
nothing but Python 3 and PyYAML.

## Quick start

```bash
# 1. Start from a commented skeleton.
python3 tools/factory/crossplane_factory.py new XBucket --group platform.hooli.tech \
  > tools/factory/examples/xbucket.yaml

# 2. Edit it, then check it -- this parses the spec and self-checks the YAML and
#    the Go template it would emit, without writing anything.
python3 tools/factory/crossplane_factory.py validate tools/factory/examples/xbucket.yaml

# 3. Look at what it would write.
python3 tools/factory/crossplane_factory.py generate tools/factory/examples/xbucket.yaml --dry-run

# 4. Write it.
python3 tools/factory/crossplane_factory.py generate tools/factory/examples/xbucket.yaml
```

Or through the Makefile:

```bash
make new-api SPEC=tools/factory/examples/xbucket.yaml
make new-api SPEC=tools/factory/examples/xbucket.yaml ARGS="--dry-run"
make factory-test
```

`generate` refuses to overwrite existing files unless you pass `--force`, so
regenerating after a spec change is `--force` and re-reading the diff.

## The spec

```yaml
kind: XBucket                 # required, UpperCamelCase
group: platform.hooli.tech    # required
version: v1alpha1             # default
scope: Namespaced             # default -- Crossplane v2 has no claims
provider: aws                 # compositions/<provider>/ subdirectory
plural: xbuckets              # default: <kind lowercased> + "s"
compositionName: ...          # default: <plural>.<provider>.<group>
description: >-               # becomes a comment header on each generated file
  One sentence on what this gives a team.

fields: [...]                 # the XRD schema, and the Kyverno rules
printerColumns: [...]         # optional; defaults to the first required field
resources: [...]              # what the composition renders
kyverno: {enabled: true}      # optional
example: {name: ..., namespace: ...}
```

### `fields`

One entry per property under `spec`. These drive three outputs at once: the XRD
schema, the Kyverno rules, and whether a value gets a `{{- with }}` guard in the
template.

```yaml
fields:
  - name: location
    type: string              # string | integer | number | boolean | object | array
    description: ...          # shows up in the Backstage form
    required: true
    enum: [EU, US]
    minimum: 1                # numeric only
    maximum: 3650             # numeric only
    pattern: "^[a-z-]+$"
    default: ClusterIP
    additionalProperties: string   # object: makes it a string map
    items: string                  # array: element type
    example: default               # value used in the generated example XR
```

Use `enum` rather than `oneOf: [{pattern: ^EU$}, ...]`. Both accept the same
values, but each `oneOf` branch carries no `type`, and the scaffolder form
kubernetes-ingestor generates from the schema then fails with `Unknown field
type undefined` instead of rendering a dropdown.

### `resources`

One entry per composed resource. Each gets a unique
`setResourceNameAnnotation`, which is what Crossplane uses to match a rendered
resource to the one it already created.

```yaml
resources:
  - name: bucket                            # the resource name annotation
    apiVersion: s3.aws.m.upbound.io/v1beta1 # namespaced (.m.) for a namespaced XR
    kind: Bucket
    ready: provider                         # provider | always | replicas
    replicasField: replicas                 # ready: replicas only
    metadataName: {fromXR: name}            # default; `false` leaves naming to Crossplane
    labels: {...}
    annotations: {...}
    providerConfigRef:
      kind: ClusterProviderConfig           # default
      fromField: providerName               # or `name: default`
    spec: {...}                             # anything; expressions allowed at any depth
```

Do not set `namespace` on a composed resource: Crossplane places it in the XR's
own namespace, and the factory rejects the key rather than let it look
otherwise.

### Expressions

Anywhere inside `labels`, `annotations`, `spec`, `data` or `stringData`, a
mapping with one of these keys is a template expression instead of a literal:

| Form | Renders |
| --- | --- |
| `{fromField: image}` | `{{ $spec.image \| quote }}` |
| `{fromField: size, quote: false}` | `{{ $spec.size }}` |
| `{fromField: size, default: 3}` | `{{ $spec.size \| default 3 }}` |
| `{fromXR: name}` | `{{ $name }}` |
| `{fromXR: namespace}` | `{{ $xr.metadata.namespace }}` |
| `{map: {field: location, values: {EU: eu-north-1, US: us-east-2}}}` | `{{ index (dict "EU" "eu-north-1" "US" "us-east-2") $spec.location }}` |
| `{template: 'ternary "Enabled" "Suspended" $spec.versioning'}` | verbatim, for anything the forms above do not cover |

Three behaviours are inferred rather than spelled out, because getting them
wrong is the common bug:

- **Optional guarding.** A field that is neither `required` nor `default`ed may
  be absent, so it is wrapped in `{{- with $spec.x }}` / `{{- end }}` and the
  key disappears when unset. Anything guaranteed present is rendered inline.
  `optional: true|false` overrides the inference.
- **String maps.** A field declared `type: object` with `additionalProperties:
  string` renders as a `range` with `| quote`d values, not a bare interpolation
  -- the shape provider `tags` fields want.
- **Defaults and quoting.** A field with a schema `default` also gets
  `| default <value>` in the template, so the composition still renders
  correctly against an XR that skipped API-server defaulting. String fields are
  `| quote`d so an image tag or ARN cannot parse as a number or a bool.

### `ready`

`function-go-templating` does not derive XR readiness on its own, and
`function-auto-ready` only propagates readiness from resources that report a
`Ready` condition.

- `provider` (default) -- a managed resource reports `Ready` itself; leave it to
  `function-auto-ready`.
- `always` -- functional the moment it exists (a Service, a ConfigMap). Sets
  `gotemplating.fn.crossplane.io/ready: "True"`.
- `replicas` -- a Deployment reports `Available`, not `Ready`, so the XR would
  sit at `Creating` forever. Reads `status.availableReplicas` off the observed
  resource and marks it ready only once the pods actually are.

### `kyverno`

Enabled by default. Every `enum` becomes an `AllNotIn` deny, every
`minimum`/`maximum` a `GreaterThan`/`LessThan` deny, and every `required` field
a presence deny. Optional fields get a precondition so an absent value does not
trip the rule.

The rules deliberately duplicate the XRD schema: the same policies run in CI
against the YAML in `crossplane/xrs` (`.github/workflows/validate-xrs.yaml`)
before a PR merges, where there is no API server to enforce the schema. That is
also why bounds belong in the spec even when the composition ignores them.

Set `kyverno: {enabled: false}` to skip both files, or pass `--no-kyverno`.

## After generating

1. `kyverno apply ./kyverno --resource ./crossplane/xrs` -- the same check CI runs.
2. `python3 tools/factory/test_factory.py` if you changed the factory itself.
3. Commit. No Argo CD wiring is needed: the `crossplane-system` ApplicationSet
   already syncs the whole `crossplane/xrds`, `crossplane/compositions` and
   `crossplane/xrs` trees with `directory.recurse`, so a new file in any of them
   is picked up.
4. No Backstage template is needed either. `kubernetes-ingestor` emits a
   scaffolder `Template` and an `API` entity per XRD, which is why field
   `description`s and `enum`s are worth writing -- they become the form.
5. If the composition references a provider that is not installed yet, add it
   under `crossplane/providers` first. An XRD whose composed kind has no CRD
   will sync fine and then fail at render time.

## Examples

- `examples/xqueue.yaml` and `examples/xmicroservice.yaml` restate the two
  manifests already in the repo. They are a fidelity check: `test_factory.py`
  asserts the factory still reproduces them, which is the only reason to trust
  its output on an API nobody has reviewed yet.
- `examples/xbucket.yaml` is an API that does not exist here, showing a
  multi-resource composition and the `template:` escape hatch. It is left
  ungenerated on purpose -- `crossplane/xrds` is synced by Argo CD, and its
  composed kinds need `provider-aws-s3`.

## Limits

- The factory writes one composition per XRD. A second composition for the same
  XRD (a different provider, say) means a second spec with a different
  `provider` and `compositionName`.
- It only emits `mode: Pipeline` compositions with go-templating and auto-ready.
  Other functions, `EnvironmentConfig` lookups and composition revisions are out
  of scope -- write those by hand.
- `validate` self-checks the generated YAML and the template's block structure.
  It cannot execute the template; `crossplane render` (which needs Docker to
  pull the functions) is still the way to see real output.
