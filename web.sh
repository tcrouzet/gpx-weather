#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$ROOT/venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "Environnement Python introuvable : $PYTHON" >&2
    echo "Créez-le puis installez les dépendances indiquées dans README.md." >&2
    exit 1
fi

cd "$ROOT"
exec "$PYTHON" app.py --web-only
