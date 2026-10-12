# SPDX-FileCopyrightText: 2024 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Builder stage: carries the full toolchain (build-essential, git, pipx/poetry,
# curl) and builds the application virtualenv; none of it reaches the runtime.
FROM python:3.13-slim-trixie AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV POETRY_VIRTUALENVS_IN_PROJECT=1

RUN apt-get update && \
  apt-get install -y --no-install-recommends curl pipx build-essential git && \
  rm -rf /var/lib/apt/lists/*

# Download and install FRP client into /usr/local/bin, verifying the
# tarball against the release's published SHA256 checksums.
ARG FRP_VERSION=0.71.0
RUN set -ex; \
    case "$(uname -m)" in \
      aarch64) FRP_ARCH="arm64" ;; \
      *) FRP_ARCH="amd64" ;; \
    esac; \
    FRP_TGZ="frp_${FRP_VERSION}_linux_${FRP_ARCH}.tar.gz"; \
    FRP_BASE="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}"; \
    echo "Downloading FRP client from $FRP_BASE/$FRP_TGZ"; \
    curl -fsSL "$FRP_BASE/$FRP_TGZ" -o "/tmp/$FRP_TGZ"; \
    curl -fsSL "$FRP_BASE/frp_sha256_checksums.txt" -o /tmp/frp_sha256_checksums.txt; \
    grep "${FRP_TGZ}\$" /tmp/frp_sha256_checksums.txt > /tmp/frp.checksum; \
    (cd /tmp && sha256sum -c frp.checksum); \
    tar -C /tmp -xzf "/tmp/$FRP_TGZ"; \
    cp "/tmp/frp_${FRP_VERSION}_linux_${FRP_ARCH}/frpc" /usr/local/bin/frpc; \
    chmod +x /usr/local/bin/frpc; \
    rm -rf "/tmp/frp_${FRP_VERSION}_linux_${FRP_ARCH}" "/tmp/$FRP_TGZ" /tmp/frp_sha256_checksums.txt /tmp/frp.checksum

RUN pipx install poetry
ENV PATH="/root/.local/bin:${PATH}"

# Install requirements into /opt/app/.venv
WORKDIR /opt/app
COPY pyproject.toml poetry.lock ./
RUN poetry install && \
    /opt/app/.venv/bin/python -m pip uninstall -y pip setuptools wheel || true

# Runtime stage: interpreter + app venv + frpc only — no build toolchain,
# no vim, no git, no curl, no pipx/poetry.
FROM python:3.13-slim-trixie

# procps provides pgrep, required by healthcheck.sh.
RUN apt-get update && \
  apt-get install -y --no-install-recommends procps && \
  rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/frpc /usr/local/bin/frpc
COPY --from=builder /opt/app/.venv /opt/app/.venv

ADD /ex_app/cs[s] /ex_app/css
ADD /ex_app/im[g] /ex_app/img
ADD /ex_app/j[s] /ex_app/js
ADD /ex_app/l10[n] /ex_app/l10n
ADD /ex_app/li[b] /ex_app/lib

COPY --chmod=775 healthcheck.sh /
COPY --chmod=775 start.sh /

WORKDIR /ex_app/lib
ENV PYTHONPATH="/"
ENTRYPOINT ["/start.sh", "/opt/app/.venv/bin/python", "main.py"]
HEALTHCHECK --interval=2s --timeout=2s --retries=300 CMD /healthcheck.sh
