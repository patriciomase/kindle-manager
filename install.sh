#!/usr/bin/env bash
# Symlink the commands onto PATH and (optionally) run the inbox as a user service.
set -euo pipefail

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
bindir="${HOME}/.local/bin"
mkdir -p "$bindir"

for cmd in "$repo"/bin/*; do
  ln -sf "$cmd" "$bindir/$(basename "$cmd")"
  echo "  linked $(basename "$cmd")"
done

# Check the login shell's PATH, not this script's: ~/.profile adds ~/.local/bin
# only when it already exists, and a non-interactive shell may not have sourced it.
login_path=$(bash -lc 'printf %s "$PATH"' 2>/dev/null || printf %s "$PATH")
case ":$login_path:" in
  *":$bindir:"*) ;;
  *) echo "! $bindir is not on your PATH — add it to ~/.bashrc" ;;
esac

if [[ "${1:-}" == "--service" ]]; then
  unitdir="${HOME}/.config/systemd/user"
  mkdir -p "$unitdir"
  sed "s#%h/Desktop/Projects/Personal/kindle-manager#${repo}#" \
    "$repo/systemd/kindle-inboxd.service" > "$unitdir/kindle-inboxd.service"
  systemctl --user daemon-reload
  systemctl --user enable --now kindle-inboxd.service
  echo "  inbox running on http://$(hostname -I | awk '{print $1}'):${KINDLE_PORT:-8137}"
  echo "  (run 'loginctl enable-linger $USER' so it survives logout)"
else
  echo
  echo "run './install.sh --service' to also start the paste-a-URL inbox on boot"
fi
