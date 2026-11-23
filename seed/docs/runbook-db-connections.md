---
title: Runbook: demasiadas conexiones a la base de datos
group: platform
---
## Síntomas
Errores "too many connections" en los servicios y latencias altas. El panel de la base de datos muestra el número de conexiones cerca del máximo.

## Causa habitual
Un servicio que abre conexiones sin pool, o que escaló a muchas réplicas y cada una tiene un pool demasiado grande.

## Solución
Identifica el servicio con más conexiones. Reduce el tamaño máximo de su pool o el número de réplicas. Como solución estructural, usa un pooler como PgBouncer delante de la base de datos.
