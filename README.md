<div align="center">

<h1>Platform Engineering | BACK Stack</h1>

_A ready-to-use environment for modern platform engineering experimentation, combining (B)ackstage, (A)rgoCD, (C)rossplane, and (K)yverno! 🚀_

<figure>
  <img src="./.docs/cover.png" alt="Interfaces and Capabilities of a Development Platform">
  <figcaption>
    <h6>
      <i>
        Interfaces and Capabilities of a Development Platform. Source: CLOUD NATIVE COMPUTING FOUNDATION. Platforms whitepaper. Available at: <a target="_blank" href="https://tag-app-delivery.cncf.io/whitepapers/platforms/#capabilities-of-platforms">https://tag-app-delivery.cncf.io/whitepapers/platforms/#capabilities-of-platforms</a>.
      </i>
    </h6>
  </figcaption>
</figure>

> _🚧 Under construction 🚧_

</div>

## Summary

- [Motivation ✨](#motivation-)
- [Stack](#stack)
- [Prerequisites](#prerequisites)
- [Up \& Running](#up--running)
  - [Troubleshooting](#troubleshooting)
  - [Accessing Applications](#accessing-applications)
- [Architecture](#architecture)
- [Roadmap 🚧](#roadmap-)
- [How to Contribute](#how-to-contribute)

## Motivation ✨

**Platform Engineering** requires integrating multiple tools to provide developers with a seamless and efficient experience. Building an **Internal Developer Platform (IDP)** involves solutions for automation, infrastructure provisioning, access control, observability, and continuous delivery workflows, which makes it challenging for both beginners and experienced teams.

Tools like **Backstage, Crossplane, and ArgoCD** are commonly used to create a unified developer experience, but experimenting with and understanding how they work together can be hard without a properly configured environment. Each technology brings its own concepts and abstraction layers, making the learning and implementation process fragmented.

This project was created to meet the need for an **easy-to-run local environment**, enabling quick experimentation with the core technologies involved in platform engineering. The goal is to provide a **functional and reproducible stack**, reducing initial complexity and enabling hands-on exploration before deploying these solutions in a production setting.

With this stack, you can:

- ✅ Quickly test integration between Backstage, Crossplane, and ArgoCD.
- ✅ Simulate an IDP experience in a local setup.
- ✅ Understand the challenges and benefits of each tool.
- ✅ Create and modify infrastructure compositions using GitOps.

If you're interested in platform engineering and want to explore how these tools fit together, this repository is a great place to start! 🚀

## Stack

This repository brings together essential tools to build and experiment with a local **Internal Developer Platform (IDP)**. Below is a brief description of each component:

- **Backstage**: An open-source developer portal created by Spotify, designed to unify tools, services, and documentation into a single interface. It provides a **service catalog**, allowing teams to organize and discover APIs, infrastructure, and documentation centrally, promoting standardization and development efficiency.

- **ArgoCD**: A GitOps continuous delivery controller for Kubernetes, responsible for managing and syncing applications defined via Git-based manifests.

- **Crossplane**: A Kubernetes-native infrastructure provisioning tool that enables declarative cloud and on-prem resource management via **Compositions**.

- **Kyverno**: A policy engine for Kubernetes that enables enforcement and validation of compliance and security rules in clusters.

- **LocalStack**: A fully functional local AWS cloud emulator that enables developers to test and build applications interacting with AWS services without needing a real AWS account.

- **Crossview**: A web-based UI for visualizing and managing Crossplane resources. It provides an intuitive interface to explore XRDs, Compositions, and composite resources, helping users understand the control plane structure and accelerate troubleshooting.

- **Helm**: A package manager for Kubernetes that simplifies deploying complex applications using reusable _charts_.

- **kind** (Kubernetes in Docker): A tool for running local Kubernetes clusters using Docker containers, ideal for testing and development.

- **kubectl**: The official Kubernetes command-line tool (CLI) for interacting with clusters, applying configurations, and managing resources.

## Prerequisites

Make sure the following dependencies are installed before running any commands:

- [`kind`](https://kind.sigs.k8s.io/docs/user/quick-start/) (version v0.27.0 or higher);
- [`kubectl`](https://kubernetes.io/docs/tasks/tools/) (version v1.32.2 or higher);
- [`argocd`](https://argo-cd.readthedocs.io/en/stable/cli_installation/) (version v2.14.2 or higher);
- [`helm`](https://helm.sh/docs/intro/install/) (version v3.17.1 or higher);
- [`yq`](https://github.com/mikefarah/yq) (version v4.45.1 or higher);
- [Docker](https://docs.docker.com/engine/install/) (version 27.4.0 or higher);
- [`skaffold`](https://skaffold.dev/docs/install/) (version v2.24.0 or higher) — optional, only needed for the Backstage dev loop described below.

> _⚠️ Installation of these basic tools is not covered here as it varies by operating system. The following scripts assume you have each one properly set up._

Before proceeding, make sure you have also completed the following steps:

1. **Fork this repository** to your GitHub account.
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/<your-username>/<repository-name>.git
   cd <repository-name>
   ```
3. **Create a `.env` file** at the root of the project based on the provided `.env.example`:
   ```bash
   cp .env.example .env
   ```
4. **Generate a GitHub Personal Access Token** with full `repo` permissions by visiting [https://github.com/settings/tokens/new](https://github.com/settings/tokens/new).
   ![https://github.com/settings/tokens/new](.docs/github-com-settings-tokens-new.png)
5. **Update the `.env` file** with your generated token, replacing the `"placeholder"` value.

> 💡 The GitHub token will be used to open pull requests in the repository and to read the catalog file for loading into Backstage.

## Up & Running

This project uses a Makefile to simplify environment setup and teardown. Follow the steps below to get started.

If you don't have the `make` command in your environment, install it according to your operating system:

<details>
  <summary>Ubuntu/Debian</summary>

```sh
sudo apt update && sudo apt install -y make
```

Verify installation:

```sh
make --version
```

Install build tools if needed:

```sh
sudo apt update && sudo apt install -y build-essential
```

</details>

<details>
  <summary>Fedora</summary>

```sh
sudo dnf install -y make
```

Verify installation:

```sh
make --version
```

Install build tools if needed:

```sh
sudo dnf groupinstall -y "Development Tools"
```

</details>

<details>
  <summary>Arch Linux</summary>

```sh
sudo pacman -Syu make
```

Verify installation:

```sh
make --version
```

Install build tools if needed:

```sh
sudo pacman -Syu base-devel
```

</details>

<details>
  <summary>macOS (via Homebrew)</summary>

```sh
brew install make
```

Verify installation:

```sh
make --version
```

Install Xcode development tools if needed:

```sh
xcode-select --install
```

</details>

<details>
  <summary>Windows (via MSYS2)</summary>

1. Download and install [MSYS2](https://www.msys2.org/).
2. Open the MSYS2 terminal and run:

```sh
pacman -S make
```

Verify installation:

```sh
make --version
```

For a complete development environment:

```sh
pacman -S base-devel
```

</details>

<br/>

To set up the environment, run:

```sh
make up
```

This command will:

- Check if required dependencies are installed.
- Create a Kubernetes cluster named `platform` (if it does not already exist).
- Run bootstrap scripts for LocalStack, Crossplane, Crossview, and ArgoCD.

To tear down the environment, run:

```sh
make down
```

This command will:

- Check if required dependencies are installed.
- Delete the Kubernetes `platform` cluster if it exists.

### Backstage Dev Loop (optional)

`make up` builds the Backstage image once and then leaves it alone. If you are
editing Backstage itself — plugins, `app-config.yaml`, the catalog —
[`skaffold`](https://skaffold.dev/docs/install/) gives you a rebuild-and-redeploy
loop against the same cluster:

```sh
make dev-backstage
```

It watches `backstage/`, rebuilds the image on every change, side-loads it into
the `platform` kind cluster, rolls the pod, streams the container logs, and holds
the port-forward on [http://localhost:3000](http://localhost:3000). `Ctrl-C`
stops the loop and leaves the last build running.

Two things to know before the first run:

- **Commit and push `argocd/apps/backstage/app.yaml` first.** Skaffold deploys
  the Deployment with a content-addressed image tag, which diffs against the
  `backstage:latest` recorded in Git. The `ignoreDifferences` rule in that file
  tells Argo CD to leave that one field alone; without it `selfHeal` reverts
  every redeploy within seconds. Argo CD reads `Application` objects from Git, so
  editing the file locally is not enough.
- **A real sync resets the image.** `backstage-app` syncs with `Replace=true`,
  so anything you later push under `.bootstrap/backstage/manifests/` replaces the
  Deployment wholesale and restores `backstage:latest`. Just re-run
  `make dev-backstage`.

The Skaffold config (`skaffold.yaml`) deliberately covers Backstage only. Cluster
creation, the `argocd` CLI bootstrap, secret templating from `.env`, and the
Crossplane/Kyverno readiness polling have no Skaffold equivalent and stay in
`make up` and `.bootstrap/*/up.sh`.

### Progressive Delivery (Kargo)

Kargo watches the [podinfo](https://github.com/stefanprodan/podinfo) image and
walks each new tag through nine zones — three environments spread over three
availability zones each, one namespace standing in for each cluster:

```
warehouse (new podinfo tag)
    │
    ▼
  dev1-0  canary ──┬──► dev1-1
                   └──► dev1-2
    ⋮  all three dev zones verified
    ▼
  test1-0 canary ──┬──► test1-1
                   └──► test1-2
    ⋮  all three test zones verified
    ▼
  prod1-0 canary ──┬──► prod1-1
                   └──► prod1-2
```

Zone 0 of each environment is its canary: Freight lands there first and the two
sibling zones promote in parallel only after it verifies. The next environment
opens only once **all three** zones of the previous one have verified — that is
`sources.availabilityStrategy: All` on the canary Stage; Kargo's default would
let a single verified zone open the gate.

Verification is a real check, not a formality: after each promotion an Argo
Rollouts `AnalysisTemplate` curls podinfo's `/healthz` through the Service that
Crossplane composed, three times, in the zone that was just promoted. A failure
stops the rollout there instead of reporting it afterwards.

#### Rendered manifests, not templated ones

A promotion does not edit a manifest in `main`. It renders one and commits the
result to that zone's own branch:

| Branch                 | Holds                                                        |
| ---------------------- | ------------------------------------------------------------ |
| `main`                 | The Helm chart and the values chain — the *source*.           |
| `stage/<zone>` (×9)    | `manifests.yaml`, fully rendered — the *desired state*.       |

Each Argo CD `Application` tracks `stage/<zone>` rather than a path in `main`,
so nothing re-renders at sync time and `git show stage/prod1-0:manifests.yaml`
is an honest answer to "what is running in that zone?". The promotion steps are
in `kargo/stages/<zone>.yaml`: clone `main` and the stage branch, `git-clear`
the branch, `helm-template` into it, commit, push, then wait for Argo CD to
report the zone healthy before the analysis starts.

#### The values chain

`delivery/` holds the source, and values are layered narrowest-last:

```
delivery/
├── chart/                            # 1. chart defaults — values.yaml
└── envs/
    ├── dev/
    │   ├── values-env.yaml           # 2. environment policy (replicas: 1)
    │   ├── dev1-0/values-cluster.yaml  # 3. this zone only (identity, overrides)
    │   ├── dev1-1/values-cluster.yaml
    │   └── dev1-2/values-cluster.yaml
    ├── test/…
    └── prod/                         #    values-env.yaml sets replicas: 2
```

The promoted image tag appears in none of them — it comes from Kargo through
`setValues` at render time, so no file in `main` can claim a version that is not
actually deployed. Render any zone exactly the way the pipeline does with:

```sh
helm template podinfo ./delivery/chart --namespace prod1-0 \
  -f ./delivery/envs/prod/values-env.yaml \
  -f ./delivery/envs/prod/prod1-0/values-cluster.yaml
```

The nine `stage/*` branches are seeded by `.bootstrap/kargo/up.sh` on first run,
which needs `GITHUB_TOKEN` in `.env` to have write access to your fork.
Branches that already exist are left alone.

### Troubleshooting

If you encounter issues, ensure that:

- All required binaries are installed and available in your system `PATH`.
- The `kind` clusters are running before applying any `kubectl` instructions.

For additional help, refer to the documentation links in the prerequisites section.

### Accessing Applications

Applications are exposed via `nohup` + `kubectl port-forward`.

| Application | Address                                        | Notes                                                                                           |
| ----------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Backstage   | [http://localhost:3000](http://localhost:3000) | Enter as a Guest User.                                                                          |
| Argo CD     | [http://localhost:8080](http://localhost:8080) | Username: `admin` <br/> Password: `12345678`                                                    |
| Crossview   | [http://localhost:3001](http://localhost:3001) | -                                                                                               |
| Kargo       | [https://localhost:3002](https://localhost:3002) | Username: `admin` <br/> Password: `admin` <br/> Serves TLS with a self-signed cert.             |
| Localstack  | [http://localhost:4566](http://localhost:4566) | Manage it via: [https://app.localstack.cloud/instances](https://app.localstack.cloud/instances) |

## Architecture

Below is a high-level architecture diagram showing how the components interact:

![Architecture](.docs/high-level-architecture-current.png)

## Roadmap 🚧

This section outlines upcoming improvements and planned changes for this project:

- [ ] Reduce the responsibility of the `.bootstrap/**/up.sh` scripts: shift tool installation and configuration to ArgoCD so that it manages not only Crossplane resources but also the cluster setup itself — making the environment closer to real-world GitOps practices.

- [ ] Improve the Kyverno GitHub Action: update the CI pipeline to apply only the policies related to the resources changed in a given Pull Request.

- [x] Evaluate the use of the **TeraSky Kubernetes Ingestor plugin** for Backstage ([link](https://github.com/TeraSky-OSS/backstage-plugins/tree/main/plugins/kubernetes-ingestor)): adopted at `v4.0.0`, replacing the hand-written `CrossplaneEntityProvider`. It understands Crossplane v2 natively — it branches on the XRD's `spec.scope`, so a `Namespaced` XRD has its XR ingested directly rather than through the claim v2 no longer has — and it generates a scaffolder template and an API entity per XRD, so neither has to be hand-maintained. No ingestion loop was observed on this version; documentation is still thin, so two behaviours worth recording:

  - `kubernetesIngestor.components.enabled: false` does **not** filter workloads. It short-circuits the whole entity provider and removes everything it had already tracked, which silently stops Crossplane XRs being ingested too. Exclude the platform's own namespaces with `components.excludedNamespaces` instead.
  - The API entities it generates hardcode `system: kubernetes-auto-ingested` — the value is a string literal in the plugin, not derived from `mappings.systemModel` — so that `System` has to exist in the catalog or every generated entity reports a dangling relation.

- [x] Evaluate the **TeraSky Crossplane Resources plugin** for Backstage ([link](https://github.com/TeraSky-OSS/backstage-plugins/tree/main/plugins/crossplane-resources)): adopted, together with the Kyverno policy-reports plugin and RoadieHQ's Argo CD plugin. Its `*Selector` components resolve the v1 or v2 implementation from the entity itself, so the resource table and graph work for legacy claims and v2 namespaced XRs alike. The tabs are gated on `isCrossplaneAvailable`, so they only appear for Crossplane entities. The dependency on the Kubernetes Ingestor is real — the ingestor supplies the annotations these components read — and is satisfied by the item above.

## How to Contribute

I welcome contributions! 🎉

Before starting, please take a moment to review the [Contributing Guidelines](./CONTRIBUTING.md).
