---
title: RSS de @laagendaculturaldefacu
status: APPROVED
owner: usuario
created: 2026-11-19
updated: 2026-11-19
approved: 2026-11-19
---

# RSS de @laagendaculturaldefacu

## Objetivo y alcance

Permitir que un lector RSS se suscriba a las publicaciones de la cuenta mediante una URL pública y gratuita. El proyecto es independiente de `rss-lecciones`. La primera entrega consiste en obtener y validar `feed.xml` localmente. La siguiente etapa cubre la consulta periódica, la persistencia del feed y el despliegue. El historial anterior a la ventana del embed, las historias y el texto dentro de las imágenes quedan fuera del alcance.

## Fuente y evidencia

El [embed público del perfil](https://www.instagram.com/laagendaculturaldefacu/embed/) devolvió HTTP 200 el 19 de noviembre de 2026 en una petición sin cookies de sesión. El HTML incluyó seis publicaciones con identificador y fecha de origen. Tres tenían texto y las vistas individuales de las otras tres tampoco aportaron texto descriptivo. Los seis enlaces originales respondieron con HTTP 200. Las seis imágenes principales estaban disponibles sin sesión mediante enlaces temporales del CDN de Instagram. Open RSS había devuelto 503 en la prueba informada por el usuario.

## Requisitos

- **REQ-01** Generar un RSS 2.0 en UTF-8 con fecha original, enlace permanente, identificador estable y extracto de hasta 20 palabras cuando haya texto. Indicar explícitamente cuando el texto no esté disponible.
- **REQ-02** Incorporar cada publicación una sola vez y conservar las entradas previas cuando salgan de la ventana de publicaciones recientes.
- **REQ-03** Conservar el último RSS válido ante errores de descarga, redirecciones a inicio de sesión, respuestas vacías o formatos inesperados.
- **REQ-04** Consultar cada dos horas desde GitHub Actions y publicar el RSS en GitHub Pages desde un repositorio público independiente. La lectura y la consulta de Instagram deben funcionar sin cuenta de Instagram y sin servicios pagos.
- **REQ-05** Mostrar una imagen principal por publicación cuando esté disponible, incluidas las entradas sin texto. Para los carruseles usar la primera imagen. Incorporar las imágenes de las entradas ya publicadas que sigan visibles en la fuente.

## Diseño y decisiones

| ID | Decisión | Motivo |
| --- | --- | --- |
| DEC-01 | Leer los datos estructurados del embed público y verificar la cuenta. | La petición local devolvió los metadatos requeridos. El formato interno puede cambiar y Open RSS presentó un 503. |
| DEC-02 | Usar `/p/<shortcode>/` como enlace y GUID de cada item. Leer el feed guardado, agregar publicaciones nuevas por shortcode y ordenar por fecha de origen. | El lector conserva la identidad de cada entrada y el historial sobrevive a la ventana corta del embed. |
| DEC-03 | Validar el candidato completo antes de reemplazar el archivo y serializar las actualizaciones. Guardar los cambios válidos en Git y desplegar explícitamente con GitHub Actions. | Git permite recuperar el feed. Un push con el token del workflow por sí solo no dispara un build de Pages. |
| DEC-04 | Usar un runner estándar y programar al minuto 33 de cada segunda hora UTC, de modo que la primera ejecución pueda verificarse durante la entrega. Habilitar una ejecución manual para probar y recuperar despliegues. | Pages y los runners estándar son gratuitos en repositorios públicos. GitHub puede demorar o saltar ejecuciones y desactivar la programación tras 60 días de inactividad del repositorio. |
| DEC-05 | Guardar una vista previa de alrededor de 640 píxeles por shortcode en `media/` y desplegarla junto al RSS. Referenciar la misma URL en la descripción HTML y en Media RSS. | Las URLs del CDN caducan. La imagen guardada sigue disponible al salir de la ventana del embed. Solo se guardan imágenes de un CDN admitido cuya firma, MIME y tamaño coincidan con el formato esperado. |

Fuentes técnicas de las decisiones. [RSS 2.0](https://www.rssboard.org/rss-specification), [Media RSS](https://www.rssboard.org/media-rss), [publicación con Actions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site), [costos de Actions](https://docs.github.com/en/actions/concepts/billing-and-usage), [programación de workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Aceptación y verificación

| ID | Requisito | Verificación y evidencia |
| --- | --- | --- |
| AC-01 | REQ-01 | **Verificado localmente.** Un segundo analizador XML comprobó la estructura del canal, las fechas de la fuente, el orden descendente, los seis GUID y enlaces únicos, los tres extractos y los tres avisos de falta de texto. |
| AC-02 | REQ-02 | **Verificado con pruebas locales.** Una respuesta repetida no cambió el archivo. Una ventana superpuesta conservó las entradas anteriores y añadió el shortcode nuevo una sola vez. |
| AC-03 | REQ-03 | **Verificado con pruebas locales.** Fallos de red, HTTP 429, inicio de sesión, una lista vacía, cambios de esquema y XML inválido dejaron intactos los bytes del RSS anterior. |
| AC-04 | REQ-04 | La [prueba manual en el runner estándar](https://github.com/santiagofj/rss-ig/actions/runs/44565856162) obtuvo seis publicaciones válidas sin sesión. El [primer despliegue](https://github.com/santiagofj/rss-ig/actions/runs/44566229771) sirvió un RSS público válido. Sigue pendiente confirmar la primera ejecución programada. |
| AC-05 | REQ-05 | Las pruebas cubren la primera imagen del carrusel, las URLs no permitidas y la conservación del feed cuando falla la descarga. La prueba local del embed obtuvo seis imágenes. Queda comprobar su descarga desde el runner y sus URLs públicas tras el despliegue. |

## Publicación y recuperación

Probar primero la descarga anónima desde el runner. Una vez validada, publicar el último feed bueno y habilitar el cron. Si falla un despliegue, reintentar con el XML validado que quedó en Git, sin depender de que hayan aparecido publicaciones nuevas. Registrar el estado de la consulta y los recuentos de entradas para identificar fallos o duplicados.

## Aprobación

El usuario aprobó el 19 de noviembre de 2026 el generador recurrente, la prueba en GitHub Actions y el despliegue en un repositorio público independiente. La actualización programada se habilita tras verificar el acceso al embed desde el runner.

Ese mismo día aprobó agregar la imagen principal, también a las entradas existentes sin texto. Las imágenes se despliegan junto con el RSS tras validar todas las referencias.
