#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env.local"
venv_dir="${project_dir}/.venv"

if [[ ! -f "${env_file}" ]]; then
    printf 'Error: required configuration file is missing: %s\n' "${env_file}" >&2
    exit 1
fi
if [[ ! -x "${venv_dir}/bin/uvicorn" ]]; then
    printf 'Error: existing virtual environment or uvicorn is missing at %s\n' "${venv_dir}" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

required_vars=(
    APP_PUBLIC_BASE_URL
    DASHBOARD_USERNAME
    DASHBOARD_PASSWORD_HASH
    SESSION_SIGNING_KEY
    EBAY_TOKEN_ENCRYPTION_KEY
    EBAY_ENV
)
for name in "${required_vars[@]}"; do
    value="${!name-}"
    if [[ -z "${value}" || "${value}" == REPLACE_WITH_* ]]; then
        printf 'Error: %s is missing or still contains a placeholder in %s\n' "${name}" "${env_file}" >&2
        exit 1
    fi
done

# Production was deliberately unlocked on 2026-09-07 for read-only market
# pricing (Browse API). This still rejects anything that isn't one of the
# two known values, so a typo can't silently start the service in an
# unintended mode. Note what production does NOT unlock: the eBay adapter
# has no mutation methods and listing_pipeline/publish.py is still an
# unimplemented placeholder, so no code path can create or edit a listing
# regardless of this setting.
if [[ "${EBAY_ENV}" != "sandbox" && "${EBAY_ENV}" != "production" ]]; then
    printf 'Error: EBAY_ENV must be sandbox or production\n' >&2
    exit 1
fi

cd -- "${project_dir}"
source "${venv_dir}/bin/activate"
exec uvicorn automation_control.api:app --host 127.0.0.1 --port 8080
