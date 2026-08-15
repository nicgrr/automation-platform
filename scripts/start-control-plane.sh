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

if [[ "${EBAY_ENV}" != "sandbox" ]]; then
    printf 'Error: EBAY_ENV must be sandbox\n' >&2
    exit 1
fi

cd -- "${project_dir}"
source "${venv_dir}/bin/activate"
exec uvicorn automation_control.api:app --host 127.0.0.1 --port 8080
