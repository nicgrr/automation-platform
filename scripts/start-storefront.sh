#!/usr/bin/env bash
set -euo pipefail

# NL Vault storefront -- the public shop, run as its own process on its own
# port, separate from the admin control plane on 8080. Keeping them as two
# apps means an auth mistake in the admin app can never expose inventory
# management or credentials through the shop, and vice versa.

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_dir}/.env.local"
venv_dir="${project_dir}/.venv"

if [[ ! -x "${venv_dir}/bin/uvicorn" ]]; then
    printf 'Error: virtual environment or uvicorn is missing at %s\n' "${venv_dir}" >&2
    exit 1
fi

# The storefront only needs the database; it deliberately does not require
# the admin app's secrets. Load .env.local if present (for STRIPE_* and the
# database URL) but don't fail without it.
if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
fi

cd -- "${project_dir}"
source "${venv_dir}/bin/activate"
exec uvicorn storefront.app:app --host 127.0.0.1 --port 8090
