# Shared by deploy/pull scripts. Loads gitignored .env.deploy on your Mac (not on GitHub).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_read_deploy_var() {
  local key="$1"
  local default_value="${2:-}"
  local file="${ROOT}/.env.deploy"
  if [[ ! -f "${file}" ]]; then
    printf '%s' "${default_value}"
    return
  fi
  local line
  line="$(grep -E "^[[:space:]]*${key}=" "${file}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    printf '%s' "${default_value}"
    return
  fi
  line="${line#*=}"
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  if [[ "${line}" == \"*\" ]]; then
    line="${line:1:${#line}-2}"
  elif [[ "${line}" == \'*\' ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "${line}"
}

SERVER="$(_read_deploy_var SERVER "ubuntu@YOUR_SERVER")"
REMOTE_DIR="$(_read_deploy_var REMOTE_DIR "~/apps/MouseXGene")"
