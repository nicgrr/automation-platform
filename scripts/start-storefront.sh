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
    printf 'Error: uvicorn is missing from %s\n' "${venv_dir}" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

cd -- "${project_dir}"
"${venv_dir}/bin/python" - <<'PY'
from sqlalchemy.orm import Session
from automation_control.database import engine
from automation_control.release_info import require_database_revision

with Session(engine) as session:
    require_database_revision(session)
PY

exec "${venv_dir}/bin/uvicorn" storefront.app:app --host 127.0.0.1 --port 8090
