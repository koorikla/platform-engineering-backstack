# Multi-stage build for the Backstage backend.
#
# Unlike the host-build Dockerfile documented at
# https://backstage.io/docs/deployment/docker#host-build, this one runs
# `yarn install` and the repo build *inside* Docker, so the only tool the host
# needs is Docker itself. That matters because the build compiles native
# modules (isolated-vm, better-sqlite3) which are pinned to a Node major
# version -- see `engines` in the root package.json.
#
# Build with the repo root as context:
#   docker build backstage -f backstage/packages/backend/Dockerfile --tag backstage:latest
#
# See https://backstage.io/docs/deployment/docker#multi-stage-build

# --- Stage 1: skeleton -------------------------------------------------------
# Reduce the workspace to just its package.json files. Dependency installation
# then only re-runs when a manifest or the lockfile actually changes, rather
# than on every source edit.
FROM node:24-trixie-slim AS packages

WORKDIR /app
COPY package.json yarn.lock .yarnrc.yml ./
COPY .yarn ./.yarn
COPY packages packages
COPY plugins plugins

RUN find packages plugins \! -name "package.json" -mindepth 2 -maxdepth 2 -exec rm -rf {} \+

# --- Stage 2: build ----------------------------------------------------------
FROM node:24-trixie-slim AS build

# node-gyp toolchain for isolated-vm (used by the scaffolder) and better-sqlite3.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends python3 g++ build-essential libsqlite3-dev && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHON=/usr/bin/python3

USER node
WORKDIR /app

COPY --from=packages --chown=node:node /app .

RUN --mount=type=cache,target=/home/node/.cache/yarn,sharing=locked,uid=1000,gid=1000 \
    yarn install --immutable

COPY --chown=node:node . .

# `--all` builds packages/app as well as packages/backend. The backend depends
# on the app workspace (`link:../app`), and app-backend serves that build
# output, so a backend-only build would produce an image with no frontend.
RUN yarn build:all

RUN mkdir -p packages/backend/dist/skeleton packages/backend/dist/bundle && \
    tar xzf packages/backend/dist/skeleton.tar.gz -C packages/backend/dist/skeleton && \
    tar xzf packages/backend/dist/bundle.tar.gz -C packages/backend/dist/bundle

# --- Stage 3: runtime --------------------------------------------------------
FROM node:24-trixie-slim

# better-sqlite3 is rebuilt during the production install below, so the
# toolchain is needed here too.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends python3 g++ build-essential libsqlite3-dev && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHON=/usr/bin/python3

# This switches many Node.js dependencies to production mode.
ENV NODE_ENV=production

# The scaffolder's isolated-vm requires the snapshot to be disabled.
ENV NODE_OPTIONS="--no-node-snapshot"

USER node
WORKDIR /app

# Install production dependencies from the skeleton, so this layer is cached
# independently of the application bundle.
COPY --from=build --chown=node:node /app/.yarn ./.yarn
COPY --from=build --chown=node:node /app/.yarnrc.yml ./
COPY --from=build --chown=node:node /app/yarn.lock /app/package.json ./
COPY --from=build --chown=node:node /app/packages/backend/dist/skeleton/ ./

RUN --mount=type=cache,target=/home/node/.cache/yarn,sharing=locked,uid=1000,gid=1000 \
    yarn workspaces focus --all --production && rm -rf "$(yarn cache clean)"

# The built backend bundle, then the files it reads at runtime.
COPY --from=build --chown=node:node /app/packages/backend/dist/bundle/ ./
COPY --chown=node:node examples ./examples
COPY --chown=node:node catalog ./catalog
COPY --chown=node:node app-config*.yaml ./

CMD ["node", "packages/backend", "--config", "app-config.yaml", "--config", "app-config.production.yaml"]
