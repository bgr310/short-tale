#!/usr/bin/env bash
# Install the secret check as a pre-push hook.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .git/hooks
cat > .git/hooks/pre-push <<'HOOK'
#!/usr/bin/env bash
exec ./scripts/check_secrets.sh
HOOK
chmod +x .git/hooks/pre-push
echo "installed .git/hooks/pre-push -> scripts/check_secrets.sh"
