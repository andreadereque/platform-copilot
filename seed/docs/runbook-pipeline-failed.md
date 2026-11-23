---
title: Runbook: un pipeline ha fallado
group: platform
---
## Síntomas
Llega una alerta de pipeline fallido o un dominio avisa de que su data product no se ha actualizado. En el panel de ejecuciones, la ejecución aparece en rojo.

## Diagnóstico
Abre los logs de la última ejecución y busca el primer error, no el último. Las causas más comunes son un cambio de esquema en la fuente, credenciales caducadas y falta de memoria en el job.

## Reintentar
Los pipelines son idempotentes: relanzarlos no duplica datos. Reintenta una vez desde el panel. Si vuelve a fallar con el mismo error, no reintentes en bucle.

## Cuándo escalar
Escala al dueño del data product si el error está en la fuente. Abre una incidencia de severidad 2 si afecta a un dominio entero.
