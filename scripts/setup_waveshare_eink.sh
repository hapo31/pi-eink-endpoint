#!/bin/bash
# Raspberry Pi OS setup for the Waveshare 2.9-inch V3 Python driver.
# https://www.waveshare.com/wiki/2.9inch_e-Paper_Module_Manual#Python
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: bash setup_waveshare_eink.sh [service-user]

Run on the Raspberry Pi 3 with Raspberry Pi OS (Bookworm or later).
Installs Python dependencies, enables SPI, and grants GPIO/SPI access.
The user defaults to the invoking user (SUDO_USER when using sudo).
When running directly as root, specify the non-root service user.
The Waveshare driver is supplied separately by the application deployment.
Reboot after setup; this script does not reboot or start the display service.
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
if (( $# > 1 )) || [[ "${1:-}" == -* ]]; then
    usage >&2
    exit 1
fi

TARGET_USER="${1:-${SUDO_USER:-$(id -un)}}"
if ! TARGET_UID="$(id -u -- "$TARGET_USER" 2>/dev/null)"; then
    echo "User does not exist: $TARGET_USER" >&2
    exit 1
fi
if [[ "$TARGET_UID" == "0" ]]; then
    echo "Specify the non-root user that will run pi-eink-endpoint." >&2
    exit 1
fi

for command in apt-get raspi-config usermod runuser; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing $command. Run this script on the Pi 3 with Raspberry Pi OS." >&2
        exit 1
    fi
done

SUDO=()
if [[ "$(id -u)" != "0" ]]; then
    SUDO=(sudo)
fi

trap 'echo "Waveshare setup failed at line $LINENO; fix the error and rerun." >&2' ERR

echo "Installing Waveshare Python dependencies..."
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y \
    python3 \
    python3-pil \
    python3-numpy \
    python3-gpiozero \
    python3-lgpio \
    python3-rpi.gpio \
    python3-spidev \
    rsync

echo "Enabling SPI0..."
"${SUDO[@]}" raspi-config nonint do_spi 0

echo "Granting GPIO/SPI access to $TARGET_USER..."
"${SUDO[@]}" usermod -aG gpio,spi -- "$TARGET_USER"

echo "Checking system Python dependencies as $TARGET_USER..."
"${SUDO[@]}" runuser -u "$TARGET_USER" -- /usr/bin/python3 -c \
    'from PIL import Image; import gpiozero, lgpio, numpy, spidev, RPi.GPIO; print("Dependencies OK")'

if [[ -e /dev/spidev0.0 ]]; then
    echo "SPI device found: /dev/spidev0.0"
else
    echo "SPI device is not present yet; check /dev/spidev0.0 after reboot."
fi

echo "Waveshare setup complete for $TARGET_USER."
echo "Run sudo reboot to apply SPI and group changes."
echo "Then deploy pi-eink-endpoint and register its service using README.md."
