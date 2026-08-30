#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Refuse to let a credential reach a public repo.
#
# Run before every push:   ./scripts/check_secrets.sh
# Or install it as a hook: ./scripts/install_hooks.sh
# ---------------------------------------------------------------------------
set -uo pipefail
cd "$(dirname "$0")/.."

FAIL=0
red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }

echo
echo "  secret check"
echo "  --------------------------------------------------------------"

# --- 1. files that must never be tracked ----------------------------------
FORBIDDEN=(".env" "profiles/" "publisher_profile/" "cookies.json" "state.json"
           "config/settings.yml")
for f in "${FORBIDDEN[@]}"; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    red "  TRACKED: $f is in git. Remove it:  git rm --cached '$f'"
    FAIL=1
  fi
done

# --- 2. anything that looks like a key in tracked content -----------------
PATTERNS=(
  'REDDIT_CLIENT_SECRET[[:space:]]*[:=][[:space:]]*[A-Za-z0-9_-]{15,}'
  'PEXELS_API_KEY[[:space:]]*[:=][[:space:]]*[A-Za-z0-9]{20,}'
  'AKIA[0-9A-Z]{16}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'gh[pousr]_[A-Za-z0-9]{30,}'
  'sk-[A-Za-z0-9]{32,}'
  '"cookies"[[:space:]]*:[[:space:]]*\['
)
TRACKED=$(git ls-files 2>/dev/null | grep -vE '^(\.env\.example|docs/|README\.md|scripts/check_secrets\.sh|\.gitleaks\.toml)' || true)
if [ -n "$TRACKED" ]; then
  for pat in "${PATTERNS[@]}"; do
    HITS=$(echo "$TRACKED" | tr '\n' '\0' | xargs -0 grep -lEI "$pat" 2>/dev/null || true)
    if [ -n "$HITS" ]; then
      red "  MATCH: pattern /$pat/ found in:"
      echo "$HITS" | sed 's/^/         /'
      FAIL=1
    fi
  done
fi

# --- 3. campaign YAML must stay credential-free ---------------------------
for f in config/campaigns/*.y*ml; do
  [ -e "$f" ] || continue
  if grep -qEi '(client_secret|password|api_key|apikey|bearer)[[:space:]]*:[[:space:]]*[^[:space:]#]' "$f"; then
    red "  CREDENTIAL: $f contains what looks like a secret. Move it to .env."
    FAIL=1
  fi
done

# --- 4. gitleaks, if it's installed ---------------------------------------
if command -v gitleaks >/dev/null 2>&1; then
  echo "  running gitleaks..."
  if ! gitleaks detect --no-banner --redact -c .gitleaks.toml 2>&1 | tail -5; then
    FAIL=1
  fi
else
  ylw "  note: gitleaks not installed (optional deeper scan)"
  ylw "        brew install gitleaks   |   https://github.com/gitleaks/gitleaks"
fi

echo "  --------------------------------------------------------------"
if [ "$FAIL" -eq 0 ]; then
  grn "  clean — safe to push"
  echo
  exit 0
fi
red "  FOUND PROBLEMS — do not push until these are fixed"
echo
exit 1
