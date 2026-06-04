# Shared by deploy/pull scripts. Loads gitignored .env.deploy on your Mac (not on GitHub).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT}/.env.deploy" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env.deploy"
  set +a
fi
SERVER="${SERVER:-ubuntu@YOUR_SERVER}"
REMOTE_DIR="${REMOTE_DIR:-~/apps/MouseXGene}"
