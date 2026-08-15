#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="${project_dir}/.venv/bin/python"

if [[ ! -x "${venv_python}" ]]; then
    printf 'Error: existing virtual environment was not found at %s\n' "${project_dir}/.venv" >&2
    exit 1
fi

cd -- "${project_dir}"
exec "${venv_python}" -m automation_control.secrets_init
