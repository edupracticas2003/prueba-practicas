#!/bin/sh
# Valida CHANGELOG.md para Jenkins (sin Python).
# Zona horaria de "hoy": Europe/Madrid (TZ / CHANGELOG_TZ).
# 1) Cabecera superior: ## Versión: [COM-LABELS] vX.Y.Z - DD/MM/YYYY = hoy
# 2) En esa sección, al menos un ID Jira (ETI-10) en
#    Nuevas Funcionalidades, Corrección de Errores o Mejoras.
set -eu

CHANGELOG_TZ="${CHANGELOG_TZ:-Europe/Madrid}"
export TZ="${TZ:-$CHANGELOG_TZ}"

TODAY="${CHANGELOG_TODAY:-$(date +%d/%m/%Y)}"
FILE="${1:-CHANGELOG.md}"

if [ ! -f "$FILE" ]; then
    echo "ERROR: no se encontró $FILE" >&2
    exit 1
fi

header=$(awk '
    /^## Versión: \[COM-LABELS\] v[0-9]+\.[0-9]+\.[0-9]+ - [0-9][0-9]\/[0-9][0-9]\/[0-9][0-9][0-9][0-9]/ {
        print
        exit
    }
' "$FILE")

if [ -z "$header" ]; then
    echo "CHANGELOG.md no válido para publicar:"
    echo "  - No se encontró ninguna cabecera '## Versión: [COM-LABELS] vX.Y.Z - DD/MM/YYYY'."
    exit 1
fi

version=$(printf '%s\n' "$header" | sed 's/^## Versión: \[COM-LABELS\] v\([0-9.]*\) - .*$/\1/')
chg_date=$(printf '%s\n' "$header" | sed 's/^.* - //')

failed=0

if [ "$chg_date" != "$TODAY" ]; then
    echo "CHANGELOG.md no válido para publicar:"
    echo "  - La versión superior v${version} tiene fecha ${chg_date}, pero hoy es ${TODAY} (zona ${CHANGELOG_TZ})."
    failed=1
fi

jiras=$(awk '
    BEGIN { n = 0; tracked = 0 }
    /^## Versión: \[COM-LABELS\]/ {
        n++
        if (n == 2) exit
        tracked = 0
        next
    }
    n == 1 && /^### / {
        tracked = 0
        if ($0 ~ /Nuevas Funcionalidades$/ || $0 ~ /Corrección de Errores$/ || $0 ~ /Mejoras$/) {
            tracked = 1
        }
        next
    }
    n == 1 && tracked == 1 {
        line = $0
        while (match(line, /[A-Z][A-Z0-9]+-[0-9]+/)) {
            print substr(line, RSTART, RLENGTH)
            line = substr(line, RSTART + RLENGTH)
        }
    }
' "$FILE" | awk 'NF && !seen[$0]++')

if [ -z "$jiras" ]; then
    if [ "$failed" -eq 0 ]; then
        echo "CHANGELOG.md no válido para publicar:"
    fi
    echo "  - La versión de hoy debe incluir al menos un ID Jira (p. ej. ETI-10) en Nuevas Funcionalidades, Corrección de Errores o Mejoras."
    failed=1
fi

if [ "$failed" -ne 0 ]; then
    exit 1
fi

jira_list=$(printf '%s\n' "$jiras" | awk '{ printf "%s%s", (NR>1?", ":""), $0 }')
echo "CHANGELOG OK: v${version} fecha ${chg_date} (hoy ${TODAY}, tz=${CHANGELOG_TZ}); Jira: ${jira_list}"
exit 0
