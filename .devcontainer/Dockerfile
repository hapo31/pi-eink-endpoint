FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# Keep uv in the image so setup does not depend on a host installation.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

USER vscode
