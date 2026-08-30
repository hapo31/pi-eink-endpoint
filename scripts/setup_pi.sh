#!/bin/bash

NODE_VERSION="18.20.7"

BASE_DIR="/opt/nodejs"
INSTALL_DIR="${BASE_DIR}/v${NODE_VERSION}"
CURRENT_LINK="${BASE_DIR}/current"

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "Installing dependencies..."
${SUDO} apt-get update
${SUDO} apt-get install -y \
    curl \
    ca-certificates \
    xz-utils

echo "Preparing ${INSTALL_DIR}..."
${SUDO} mkdir -p "${INSTALL_DIR}"

TMP_INSTALLER="$(mktemp)"
trap 'rm -f "${TMP_INSTALLER}"' EXIT

echo "Downloading unofficial-builds installer..."
curl -fsSL \
    https://unofficial-builds.nodejs.org/install-node.sh \
    -o "${TMP_INSTALLER}"

echo "Installing Node.js v${NODE_VERSION} for ARMv6..."
${SUDO} bash "${TMP_INSTALLER}" \
    --line "${NODE_VERSION}" \
    --platform armv6l \
    --dir "${INSTALL_DIR}" \
    --yes

echo "Creating current symlink..."
${SUDO} ln -sfn \
    "${INSTALL_DIR}" \
    "${CURRENT_LINK}"

echo "Adding Node.js to system PATH..."
printf '%s\n' \
    'export PATH="/opt/nodejs/current/bin:$PATH"' \
    | ${SUDO} tee /etc/profile.d/nodejs.sh >/dev/null

${SUDO} chmod 644 /etc/profile.d/nodejs.sh

# 現在のシェルにも即時反映
export PATH="${CURRENT_LINK}/bin:${PATH}"

echo
echo "Installation complete."
echo "Node: $(node --version)"
echo "npm : $(npm --version)"
echo "Path: $(command -v node)"
