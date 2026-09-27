# Oferta Única

**¿Cuántas empresas compitieron por este contrato?** Indicadores de competencia en la contratación pública española, por órgano de contratación, con datos oficiales de la Plataforma de Contratación del Sector Público.

Web: https://pedri77.github.io/oferta-unica/ (beta)

## Qué mide

Para cada órgano (ayuntamiento, ministerio, hospital, universidad, empresa pública…), en los últimos 12 meses:

1. **Oferta única**: lotes competitivos con una sola oferta.
2. **Negociado sin publicidad**: parte del importe adjudicado sin anuncio previo (sin emergencias).
3. **Menores pegados al umbral**: contratos menores justo por debajo del límite legal.
4. **Menores recurrentes**: menores repetidos con la misma empresa y sector que superan el límite en el año.
5. **Concentración de proveedores**: índice Herfindahl-Hirschman.
6. **Plazos cortos**: licitaciones con plazo por debajo del mínimo legal.

Cada órgano se compara con órganos del mismo tipo y tamaño. Son **indicadores de riesgo, no acusaciones**. Metodología completa en la web (`site/metodologia.html`).

## Cómo funciona

- `scripts/placsp.py`: parser en streaming de los ZIP ATOM/CODICE de la Plataforma. Guarda una fila compacta por adjudicación en SQLite, con la última versión de cada expediente. Anonimiza a las personas físicas.
- `scripts/fetch.py`: descarga solo los ficheros que han cambiado (compara el ETag; Hacienda regenera meses cerrados).
- `scripts/build.py`: calcula indicadores, percentiles entre pares y genera los JSON de la web en `site/data/`.
- `.github/workflows/actualizar.yml`: cada día a las 05:30 UTC recupera el estado (release `estado`), procesa lo nuevo, recalcula y despliega en GitHub Pages. Para cargar el histórico: ejecutar el workflow a mano con `anual = 2023 2024`.
- `site/`: web estática sin dependencias.

En local:

```bash
python3 scripts/fetch.py --db work/state.sqlite --desde 2025-01
python3 scripts/build.py --db work/state.sqlite --out site/data
python3 -m http.server -d site 8000
```

## Datos personales

Los adjudicatarios personas físicas (DNI/NIE, NIF enmascarado, comunidades de bienes y sociedades civiles) se anonimizan al procesar: nunca se guarda ni se publica su nombre o NIF. Solo se muestran empresas.

## Correcciones

Formulario de rectificación en *Issues*. Registro en `CORRECCIONES.md`.

## Licencia

Código: MIT. Datos: Plataforma de Contratación del Sector Público (Ministerio de Hacienda), reutilizados conforme a la Ley 37/2007. La Administración no patrocina este proyecto.
