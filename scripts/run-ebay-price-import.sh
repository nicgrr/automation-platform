#!/usr/bin/env bash
# Daily eBay active-listing price refresh, for systemd.
#
# Mirrors run-pokemonpricetracker-backfill.sh: load .env.local, use the
# project venv, exec the module. The two differ only in what they can cover
# in one run -- pokemonpricetracker is ~25 cards a day on its free tier,
# while eBay's Browse quota is around 5,000 calls at one per card, so the
# whole held set fits comfortably in a single pass. Hence the high default
# limit below rather than a backlog that takes a week to drain.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env.local"
venv_dir="${project_dir}/.venv"

if [[ ! -f "${env_file}" ]]; then
    printf 'Error: required configuration file is missing: %s\n' "${env_file}" >&2
    exit 1
fi
if [[ ! -x "${venv_dir}/bin/python" ]]; then
    printf 'Error: virtual environment missing at %s\n' "${venv_dir}" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

cd -- "${project_dir}"
printf -- '--- %s ---\n' "$(date -Is)"
# 2000 covers every held card with headroom (1,343 at the time of writing)
# and stays well under the daily quota. Targets are ordered stalest-first,
# so even if the set outgrows this the oldest prices are always the ones
# refreshed rather than an arbitrary slice.
exec "${venv_dir}/bin/python" -m scripts.import_ebay_prices --limit 2000 "$@"
