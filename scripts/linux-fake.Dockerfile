FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG DEBIAN_FRONTEND=noninteractive
ARG GO_VERSION=1.25.5
# The archives' SHA-256 from go.dev's release index (https://go.dev/dl/?mode=json&include=all), pinned
# here: go.dev's per-file .sha256 URLs now answer with an HTML redirect page (#1878). Update with GO_VERSION.
ARG GO_SHA256_AMD64=9e9b755d63b36acf30c12a9a3fc379243714c1c6d3dd72861da637f336ebb35b
ARG GO_SHA256_ARM64=b00b694903d126c588c378e72d3545549935d3982635ba3f7a964c9fa23fe3b9
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
        amd64) node_arch=x64; go_arch=amd64; go_sha256="$GO_SHA256_AMD64" ;; \
        arm64) node_arch=arm64; go_arch=arm64; go_sha256="$GO_SHA256_ARM64" ;; \
        *) printf 'unsupported container architecture: %s\n' "$TARGETARCH" >&2; exit 1 ;; \
    esac \
    && node_archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${node_archive}" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" \
    && grep "  ${node_archive}$" SHASUMS256.txt | sha256sum -c - \
    && tar -xJf "$node_archive" -C /usr/local --strip-components=1 \
    && go_archive="go${GO_VERSION}.linux-${go_arch}.tar.gz" \
    && curl -fsSLO "https://go.dev/dl/${go_archive}" \
    && printf '%s  %s\n' "$go_sha256" "$go_archive" | sha256sum -c - \
    && tar -xzf "$go_archive" -C /usr/local \
    && rm -f "$node_archive" SHASUMS256.txt "$go_archive"

RUN python3.11 -m venv /opt/autocode-venv \
    && /opt/autocode-venv/bin/pip install --no-cache-dir psutil==7.1.0 pytest==9.1.1 \
    # Ubuntu 24.04 images ship their own "ubuntu" user and group as 1000; free the IDs first (#1878).
    && userdel --remove ubuntu \
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
