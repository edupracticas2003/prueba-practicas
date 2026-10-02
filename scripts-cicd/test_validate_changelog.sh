#!/bin/sh
# Tests POSIX de scripts-cicd/validate_changelog.sh (sin Python).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
SCRIPT="$ROOT/scripts-cicd/validate_changelog.sh"
FAILS=0

assert_eq() {
    got=$1
    want=$2
    msg=$3
    if [ "$got" != "$want" ]; then
        echo "FAIL: $msg (got $got want $want)"
        FAILS=$((FAILS + 1))
        return 1
    fi
    echo "OK: $msg"
}

run() {
    today=$1
    file=$2
    TZ=Europe/Madrid CHANGELOG_TZ=Europe/Madrid CHANGELOG_TODAY="$today" \
        sh "$SCRIPT" "$file"
}

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

cat > "$TMP" << 'EOF'
# CHANGELOG.md

---

## Versión: [COM-LABELS] v1.0.2 - 18/09/2026

### 🚀 Nuevas Funcionalidades
* [ETI-10] - Mejorar consulta

### 🐛 Corrección de Errores
* [ETI-15] - Lecturas CLI

### 🛠️ Mejoras

---

## Versión: [COM-LABELS] v1.0.1 - 15/09/2026

### 🚀 Nuevas Funcionalidades
* [ETI-1] - Estructura básica.

### 🐛 Corrección de Errores

### 🛠️ Mejoras
EOF

set +e
out=$(run "18/09/2026" "$TMP" 2>&1)
rc=$?
set -e
assert_eq "$rc" "0" "fecha de hoy y Jira en Nuevas Funcionalidades"
printf '%s\n' "$out" | grep -q "CHANGELOG OK" || { echo "FAIL: falta CHANGELOG OK"; FAILS=$((FAILS + 1)); }
printf '%s\n' "$out" | grep -q "ETI-10" || { echo "FAIL: falta ETI-10"; FAILS=$((FAILS + 1)); }

set +e
out=$(run "19/09/2026" "$TMP" 2>&1)
rc=$?
set -e
assert_eq "$rc" "1" "fecha distinta de hoy"
printf '%s\n' "$out" | grep -q "18/09/2026" || { echo "FAIL: debe citar fecha CHANGELOG"; FAILS=$((FAILS + 1)); }
printf '%s\n' "$out" | grep -q "19/09/2026" || { echo "FAIL: debe citar hoy forzada"; FAILS=$((FAILS + 1)); }

sed 's/\[ETI-10\]/sin id/; s/\[ETI-15\]/sin id/' "$TMP" > "${TMP}.nojira"
set +e
out=$(run "18/09/2026" "${TMP}.nojira" 2>&1)
rc=$?
set -e
assert_eq "$rc" "1" "sin Jira en la versión superior (ETI-1 de v1.0.1 no cuenta)"
printf '%s\n' "$out" | grep -q "Jira" || { echo "FAIL: debe mencionar Jira"; FAILS=$((FAILS + 1)); }
printf '%s\n' "$out" | grep -q "CHANGELOG OK" && { echo "FAIL: no debe pasar con Jira solo de versión anterior"; FAILS=$((FAILS + 1)); }

cat > "${TMP}.mejoras" << 'EOF'
## Versión: [COM-LABELS] v9.9.9 - 18/09/2026

### 🚀 Nuevas Funcionalidades

### 🐛 Corrección de Errores

### 🛠️ Mejoras
* [ETI-99] - nota
EOF
set +e
out=$(run "18/09/2026" "${TMP}.mejoras" 2>&1)
rc=$?
set -e
assert_eq "$rc" "0" "Jira solo en Mejoras basta"
printf '%s\n' "$out" | grep -q "ETI-99" || { echo "FAIL: falta ETI-99"; FAILS=$((FAILS + 1)); }

rm -f "${TMP}.nojira" "${TMP}.mejoras"

if [ "$FAILS" -ne 0 ]; then
    echo "$FAILS test(s) fallaron"
    exit 1
fi
echo "Todos los tests de validate_changelog.sh OK"
exit 0
