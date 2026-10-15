FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG DEBIAN_FRONTEND=noninteractive
ARG GO_VERSION=1.25.5
ARG NODE_VERSION=22.18.0
ARG TARGETARCH

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bubblewrap \
        ca-certificates \
        curl \
        git \
        software-properties-common \
        xz-utils \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

RUN case "$TARGETARCH" in \
        amd64) node_arch=x64; go_arch=amd64 ;; \
        arm64) node_arch=arm64; go_arch=arm64 ;; \
        *) printf 'unsupported container architecture: %s\n' "$TARGETARCH" >&2; exit 1 ;; \
    esac \
    && node_archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${node_archive}" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" \
    && grep "  ${node_archive}$" SHASUMS256.txt | sha256sum -c - \
    && tar -xJf "$node_archive" -C /usr/local --strip-components=1 \
    && go_archive="go${GO_VERSION}.linux-${go_arch}.tar.gz" \
    && curl -fsSLO "https://go.dev/dl/${go_archive}" \
    && curl -fsSL "https://go.dev/dl/${go_archive}.sha256" -o go.sha256 \
    && printf '%s  %s\n' "$(cat go.sha256)" "$go_archive" | sha256sum -c - \
    && tar -xzf "$go_archive" -C /usr/local \
    && rm -f "$node_archive" SHASUMS256.txt "$go_archive" go.sha256

RUN python3.11 -m venv /opt/autocode-venv \
    && /opt/autocode-venv/bin/pip install --no-cache-dir psutil==7.1.0 \
    && groupadd --gid 1000 autocode \
    && useradd --uid 1000 --gid autocode --create-home --shell /bin/bash autocode \
    && chown -R autocode:autocode /opt/autocode-venv

ENV PATH="/opt/autocode-venv/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin" \
    HOME="/home/autocode" \
    PYTHONDONTWRITEBYTECODE="1" \
    PIP_DISABLE_PIP_VERSION_CHECK="1" \
    GOTOOLCHAIN="local"

USER autocode
WORKDIR /workspace
