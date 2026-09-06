#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${project_dir}/.venv"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
    printf 'Error: virtual environment missing at %s\n' "${venv_dir}" >&2
    exit 1
fi

cd -- "${project_dir}"
exec "${venv_dir}/bin/python" -m scripts.audit_inventory_photos "$@"
