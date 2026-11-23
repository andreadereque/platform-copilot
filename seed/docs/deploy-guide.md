---
title: Guía de despliegue de servicios de la plataforma
group: platform
---
## Flujo
Cada servicio tiene su imagen Docker, que el CI construye y publica con el hash del commit. El despliegue se hace con GitOps: un pull request al repositorio de despliegue cambia la versión y Argo CD la sincroniza en OpenShift.

## Requisitos de la imagen
La imagen debe funcionar sin root, escuchar en un puerto mayor que 1024 y exponer /healthz y /readyz.

## Rollback
Para volver a la versión anterior, revierte el pull request en el repositorio de despliegue. Argo CD aplica el cambio en minutos.
