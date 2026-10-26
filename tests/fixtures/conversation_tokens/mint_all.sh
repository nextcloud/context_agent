#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Nextcloud GmbH and Nextcloud contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Mint a conversation_token fixture for every release tag that has none yet.
#
# For each tag, the sources of the tag are run by mint.py in an environment with
# the dependencies the tag shipped with: the versions pinned in its poetry.lock,
# or, for releases before the lock file, what was the newest on the release day.
# Requires git and uv. Run it after tagging a release and commit the new fixture.
#
#   tests/fixtures/conversation_tokens/mint_all.sh [tag...]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The packages that make up the token format
PACKAGES="langgraph langgraph-checkpoint langgraph-prebuilt langgraph-sdk langchain-core langsmith ormsgpack msgpack pydantic pydantic-core typing-extensions"

tags=("$@")
if [ ${#tags[@]} -eq 0 ]; then
	mapfile -t tags < <(git -C "$REPO" tag --list 'v*' --sort=creatordate)
fi

for tag in "${tags[@]}"; do
	out="$HERE/$tag.json"
	if [ -e "$out" ] && [ $# -eq 0 ]; then
		continue
	fi
	echo "== $tag"
	src="$WORK/$tag/src"
	mkdir -p "$src"
	git -C "$REPO" archive "$tag" ex_app | tar -x -C "$src"

	# released the day of the tag, so take what was available by the end of that day
	cutoff="$(date -u -d "$(git -C "$REPO" log -1 --format=%cI "$tag") + 1 day" +%Y-%m-%dT00:00:00Z)"
	requirements=()
	if git -C "$REPO" cat-file -e "$tag:poetry.lock" 2>/dev/null; then
		for package in $PACKAGES; do
			version="$(git -C "$REPO" show "$tag:poetry.lock" | grep -A1 -x "name = \"$package\"" | sed -n 's/^version = "\(.*\)"$/\1/p' || true)"
			if [ -n "$version" ]; then requirements+=("$package==$version"); fi
		done
	else
		requirements+=(langgraph langchain-core)
	fi

	uv venv --quiet --python 3.11 "$WORK/$tag/venv"
	VIRTUAL_ENV="$WORK/$tag/venv" uv pip install --quiet --exclude-newer "$cutoff" "${requirements[@]}"
	"$WORK/$tag/venv/bin/python" -I "$HERE/mint.py" "$src" "$tag" "$out"
done
