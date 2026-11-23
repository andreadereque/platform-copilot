---
title: Cómo publicar un data product
group: public
---
## Requisitos previos
Para publicar un data product necesitas ser miembro de un dominio (sales, finance, hr, supply, quality, marketing o platform) y tener un dueño asignado. El dueño es la persona responsable de la calidad, la documentación y de aprobar los accesos.

## Pasos para publicar
1. Crea la tabla o vista en el espacio de tu dominio siguiendo las convenciones de nombres (dominio_pais_frecuencia).
2. Rellena la ficha en el catálogo: descripción, dueño, país, frecuencia de actualización y si contiene PII.
3. Añade al menos tres tests de calidad: no nulos en claves, unicidad y frescura.
4. Abre un pull request en el repositorio del dominio. El equipo de plataforma revisa la ficha y los tests.
5. Tras el merge, el data product aparece en el catálogo en menos de 15 minutos.

## Después de publicar
Revisa cada mes las peticiones de acceso y las alertas de calidad. Si el data product deja de usarse durante 90 días, la plataforma propone archivarlo.
