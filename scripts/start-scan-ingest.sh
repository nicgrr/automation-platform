#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env.local"
session_file="${project_dir}/scan_ingest_data/session.env"
venv_dir="${project_dir}/.venv"

if [[ ! -f "${env_file}" ]]; then
    printf 'Error: required configuration file is missing: %s\n' "${env_file}" >&2
    exit 1
fi
if [[ ! -f "${session_file}" ]]; then
    printf 'Error: required configuration file is missing: %s\n' "${session_file}" >&2
    printf 'Copy scan_ingest_data/session.env.example to session.env.\n' >&2
    exit 1
fi
if [[ ! -x "${venv_dir}/bin/python" ]]; then
    printf 'Error: virtual environment missing at %s\n' "${venv_dir}" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
# shellcheck disable=SC1090
source "${session_file}"
set +a

cd -- "${project_dir}"

# SET_ID unset -> auto-detect the set (and rotation) per sheet from every
# cached set, so switching piles is just "cache-set", nothing to reconfigure
# here. Set it to pin this service to one fixed set instead.
args=(-m automation_control.scan_ingest.cli scan --unattended)
[[ -n "${SET_ID:-}" ]] && args+=(--set-id "${SET_ID}")
[[ -n "${VARIANT:-}" ]] && args+=(--variant "${VARIANT}")
[[ -n "${CONDITION:-}" ]] && args+=(--condition "${CONDITION}")
[[ -n "${EXPECTED_COUNT:-}" ]] && args+=(--expected-count "${EXPECTED_COUNT}")
[[ -n "${SET_ID:-}" && -n "${ROTATE:-}" ]] && args+=(--rotate "${ROTATE}")

export PYTHONUNBUFFERED=1
exec "${venv_dir}/bin/python" "${args[@]}"
