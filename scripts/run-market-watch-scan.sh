#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env.local"
venv_dir="${project_dir}/.venv"

if [[ ! -f "${env_file}" ]]; then
    printf 'Missing configuration: %s\n' "${env_file}" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

cd -- "${project_dir}"
exec "${venv_dir}/bin/python" scripts/scan_collectible_market.py --limit 20
