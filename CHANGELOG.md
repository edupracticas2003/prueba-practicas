# CHANGELOG.md - Proceso de comunicaciones para las etiquetas y prompt de comandos

---

## Versión: [COM-LABELS] v1.0.3 - 01/10/2026

### 🚀 Nuevas Funcionalidades
* [CML-25] - Crear comando para ver el estado y el contenido de una etiqueta. Se ha cambiado el identificador del proyecto de ETI a CML.

### 🐛 Corrección de Errores

### 🛠️ Mejoras

---

## Versión: [COM-LABELS] v1.0.2 - 18/09/2026

### 🚀 Nuevas Funcionalidades
* [ETI-10] - Mejorar la consulta de estado de etiquetas como la de antenas a nivel de comando. Completar list-labels al patrón de list-antennas (filtro activo + estado Solum opcional). API org `/api/v1/labels/query-status` con el mismo auth Token-Header que antenas. list-labels acepta [label_name] opcional al final (WHERE etiqueta LIKE). STATUS AT no queda ON si type/info falla (timeout/ERROR) aunque detail tenga SUCCESS. STATUS AT muestra 🟡 TIMEOUT (no 🔴 OFF) si Solum hace connect/read timeout. Fallos Solum type/info/detail van al log, no a stdout entre filas de list-labels. currentImage.state TIMEOUT (Solum) → STATUS AT 🟡 TIMEOUT; type/info SUCCESS no lo pisa a ON. STATUS AT sale solo de labels/detail (state o HTTP != 200 → ERROR); type/info no lo gobierna. LAST CONNECTION es solo statusUpdateTime (nunca processUpdateTime); en TIMEOUT con null, usa el de otra página SUCCESS

### 🐛 Corrección de Errores
* [ETI-15] - Lecturas CLI (list-labels/list-antennas) ven commits nuevos: autocommit y fin de snapshot REPEATABLE READ

### 🛠️ Mejoras

---

## Versión: [COM-LABELS] v1.0.1 - 15/09/2026

### 🚀 Nuevas Funcionalidades
* [ETI-1] - Estructura básica.
* [ETI-2] - Realizar comando de listar antenas de cada hotel
* [ETI-3] - Realizar comando de listar etiquetas de cada hotel
* [ETI-4] - Integrar la API de Solum para saber el estado de las antenas
* [ETI-5] - Modificar el exit para incluir información
* [ETI-6] - Incluir usuario y clave de acceso
* [ETI-9] - Comprobar y verificar la llamada de la API para externos

### 🐛 Corrección de Errores

### 🛠️ Mejoras

---
