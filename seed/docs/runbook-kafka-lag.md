---
title: Runbook: lag en un consumidor de Kafka
group: platform
---
## Síntomas
El lag de un consumer group crece sin parar y los datos llegan tarde a los data products.

## Diagnóstico
Comprueba si hay un mensaje que falla siempre y bloquea la partición. Mira si el consumidor está caído o reiniciándose. Revisa si el volumen de entrada ha subido.

## Solución
Envía el mensaje que falla a la dead letter queue. Escala el número de consumidores hasta como máximo el número de particiones. Si el volumen ha subido de forma permanente, aumenta las particiones.
