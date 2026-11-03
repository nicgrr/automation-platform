#!/usr/bin/env bash
set -euo pipefail

# Runs scripts/import_ebay_orders.py (Phase 5 of the two-way storefront
# <-> eBay stock sync). Needs real eBay credentials, unlike
# run-check-held-sheets.sh's read-only job, so .env.local is sourced
# first -- see scripts/import_ebay_prices.py's own docstring for the same
# requirement and why (Settings deliberately doesn't load it itself).

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env.local"
venv_dir="${project_dir}/.venv"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
    printf 'Error: virtual environment missing at %s\n' "${venv_dir}" >&2
    exit 1
fi

if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
fi

cd -- "${project_dir}"
exec "${venv_dir}/bin/python" -m scripts.vinted_ebay_fallback --apply "$@"
