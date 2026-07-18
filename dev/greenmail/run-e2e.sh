#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
compose_file="${repository_root}/dev/greenmail/compose.yml"
compose_project="mcp-email-server-e2e-$$-${RANDOM}"
compose_started=false

cleanup() {
    if [[ "${compose_started}" == true ]]; then
        docker compose --project-name "${compose_project}" --file "${compose_file}" down --volumes --remove-orphans
    fi
}
trap cleanup EXIT

cd "${repository_root}"
docker info >/dev/null
compose_started=true
docker compose --project-name "${compose_project}" --file "${compose_file}" up --detach
smtp_port="$(docker compose --project-name "${compose_project}" --file "${compose_file}" port greenmail 3025 | sed 's/.*://')"
imap_port="$(docker compose --project-name "${compose_project}" --file "${compose_file}" port greenmail 3143 | sed 's/.*://')"
printf 'GreenMail project %s is using SMTP port %s and IMAP port %s\n' "${compose_project}" "${smtp_port}" "${imap_port}"
MCP_EMAIL_SERVER_E2E_SMTP_PORT="${smtp_port}" \
    MCP_EMAIL_SERVER_E2E_IMAP_PORT="${imap_port}" \
    uv run pytest -o addopts= -m e2e tests/e2e
