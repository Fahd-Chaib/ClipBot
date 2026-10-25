#!/usr/bin/env bash
# Sauvegarde / restauration de la mémoire du bot (file d'attente + coffre chiffré)
# sur la branche `state` : un seul commit, l'historique ne grossit pas.
#   deploy/state.sh restore   → remplit ./state
#   deploy/state.sh save      → pousse ./state
set -euo pipefail

BR=state
DIR=state
ACTION="${1:-}"

case "$ACTION" in
  restore)
    mkdir -p "$DIR"
    if git ls-remote --exit-code --heads origin "$BR" >/dev/null 2>&1; then
      git fetch --depth=1 origin "$BR"
      git archive FETCH_HEAD | tar -x -C "$DIR"
      echo "état restauré"
    else
      echo "pas encore d'état (premier passage)"
    fi
    ;;
  save)
    [ -d "$DIR" ] || { echo "rien à sauvegarder"; exit 0; }
    tmp="$(mktemp -d)"
    cp -r "$DIR"/. "$tmp"/
    (
      cd "$tmp"
      git init -q -b "$BR"
      git config user.name "clipbot"
      git config user.email "clipbot@users.noreply.github.com"
      git add -A
      git commit -qm "state $(date -u +%FT%TZ)" || exit 0
      git push -q -f "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$BR"
    )
    echo "état sauvegardé"
    ;;
  *)
    echo "usage: $0 restore|save" >&2
    exit 2
    ;;
esac
