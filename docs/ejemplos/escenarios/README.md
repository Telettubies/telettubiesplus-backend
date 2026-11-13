# Escenarios de prueba: documentos de empresa por licitación

Datos **sintéticos** para probar el análisis de Criterio de punta a punta. Cada escenario es
una empresa oferente con todos sus soportes, construida para dar un resultado conocido frente
a uno de los dos pliegos de ejemplo de `pliegos/`.

| Pliego | Escenario | Empresa | Resultado esperado |
|---|---|---|---|
| SAMC-004-2026 (mesa de ayuda, $850 M) | `samc-004-2026/A_cumple_soporte_integral` | Soporte Integral del Magdalena S.A.S. | **Presentarse**: cumple todo con holgura |
| SAMC-004-2026 | `samc-004-2026/B_no_cumple_tecnoayuda` | TecnoAyuda Express S.A.S. | **No presentarse**: incumple jurídico, financiero, experiencia, personal e ISO |
| SAMC-004-2026 | `samc-004-2026/C_limite_red_digital` | Red Digital Andina S.A.S. | **Con riesgo / revisión humana**: valores exactos en el límite y alcance ISO ambiguo |
| LP-012-2026 (SGDEA, $3.150 M) | `lp-012-2026/A_cumple_gestion_documental_inteligente` | Gestión Documental Inteligente S.A.S. | **Presentarse**: cumple todo y suma en todos los ponderables |
| LP-012-2026 | `lp-012-2026/B_no_cumple_archivo_agil` | Archivo Ágil Ltda. | **No presentarse**: incumple casi todos los capítulos |
| LP-012-2026 | `lp-012-2026/C_limite_datos_archivos_sur` | Datos y Archivos del Sur S.A.S. | **Con riesgo / revisión humana**: todo en el límite y una certificación vencida |

## Contenido de cada escenario

| Archivo | Tipo de registro que debe extraerse |
|---|---|
| `01_certificado_existencia_y_documentos_juridicos.pdf` | `empresa` (y capacidades: duración, RUP, garantía, parafiscales) |
| `02_estados_financieros_2025.pdf` | `financiero` (cifras base e indicadores) |
| `03_certificaciones_experiencia.pdf` | `contrato` (una página por contrato) |
| `04_hojas_de_vida.pdf` | `hoja_de_vida` (una página por persona) |
| `05_certificaciones_y_capacidades.pdf` | `certificacion` y `capacidad` |
| `base_conocimiento.json` | La misma información en el contrato `BaseConocimiento` (validado contra el esquema) |

Dos formas de cargarlo:

1. **Rápida**: importar `base_conocimiento.json` desde la base de conocimiento (reemplaza la base completa).
2. **Flujo documental**: subir los cinco PDF, revisar los datos extraídos y aprobarlos.

Después se carga el pliego correspondiente de `pliegos/` y se compara el resultado con las
tablas siguientes. Usa una organización distinta por escenario o reemplaza la base antes de
cada prueba, para no mezclar empresas.

## SAMC-004-2026: cierre el 24 de noviembre de 2026

| Requisito (numeral) | A: Soporte Integral | B: TecnoAyuda | C: Red Digital |
|---|---|---|---|
| Certificado de existencia ≤ 30 días (4.1) | Cumple (6-nov) | **No** (15-sep, 70 días) | Cumple en el límite (25-oct, 30 días) |
| Duración ≥ plazo + 1 año (4.1) | Cumple (2062) | **No** (jul-2027) | Cumple (dic-2028) |
| Garantía de seriedad $85 M (4.1) | Cumple | **No** (no aporta) | Cumple |
| Liquidez ≥ 1,2 (4.2) | 2,24 | 1,42 | **1,20 exacto** |
| Endeudamiento ≤ 0,70 (4.2) | 0,45 | **0,78** | **0,70 exacto** |
| Capital de trabajo ≥ $255 M (4.2) | $1.020 M | **$190 M** | **$255 M exacto** |
| Contrato ≥ $425 M, 81111800, 5 años (4.3) | $612 M | **$380 M**; otro fuera de ventana | $870 M × 50 % = **$435 M** |
| ANS ≥ 95 % (4.3) | 97,4 % | **91 %** | **95,0 % exacto** |
| Coordinador: ingeniero, 5 + 2 años, ITIL (4.4) | Cumple | **No** (tecnólogo, 3 años, sin ITIL) | Cumple en el límite (5 años desde el grado) |
| ISO 9001 vigente con alcance TI (4.5) | Cumple | **No** (vencido y alcance comercial) | **Ambiguo**: vigente, pero alcance de equipos y redes |
| 8 técnicos y mesa 7x24 (5) | 26 técnicos, 7x24 | 5 técnicos | 9 técnicos; 7x24 solo por turnos de disponibilidad |

## LP-012-2026: cierre el 30 de noviembre de 2026

| Requisito (capítulo) | A: Gestión Documental Inteligente | B: Archivo Ágil | C: Datos y Archivos del Sur |
|---|---|---|---|
| Certificado ≤ 30 días; duración ≥ plazo + 5 años (3) | Cumple (2060) | **Duración hasta 2030** | Cumple en el límite (31-oct; hasta dic-2033) |
| RUP con 81111500 y 81112200 (3) | Cumple | **Falta 81112200** | Cumple |
| Garantía $315 M por 4 meses (3) | Cumple | **Solo 2 meses** | Cumple |
| Parafiscales firmados por revisor fiscal (3) | Cumple | **Firma un contador** | Cumple |
| Liquidez ≥ 1,50 · endeudamiento ≤ 0,55 (4) | 2,10 · 0,41 | **1,10 · 0,68** | **1,50 · 0,55 exactos** |
| Cobertura de intereses ≥ 3,00 (4) | 7,80 | **1,80** | **3,00 exacto** |
| Capital de trabajo ≥ $1.260 M · patrimonio ≥ $1.575 M (4) | $1.910 M · $4.070 M | **$90 M · $890 M** | **$1.260 M exacto** · $2.160 M |
| ROE ≥ 0,10 · ROA ≥ 0,05 (4) | 0,157 · 0,093 | **0,040 · 0,013** | 0,111 · **0,050 exacto** |
| Hasta 3 contratos, 7 años, 2 códigos (5.1 a, b) | Cumple | **Uno con un solo código; otro terminado en 2019** | Cumple |
| Suma ≥ 2.250 SMMLV (5.1 c) | 1.200 + 750 + 350 = 2.300 | **480** | 1.724,14 × 60 % + 1.000 + 216 = **2.250,48** |
| Migración ≥ 500.000 documentos (5.1 d) | 1.350.000 | **200.000** (el de 650.000 está fuera de ventana) | **520.000** |
| Contrato con entidad pública (5.1 e) | Cumple | Cumple | Cumple |
| Director: posgrado, 10 + 5 años, PMP (6) | Cumple (maestría) | **Industrial sin posgrado, 6 años, PMP vencido** | Cumple en el límite (10 años exactos) |
| Líder de calidad: auditor ISO 9001 (6) | Cumple | **No aportado** | Cumple en el límite (5 años) |
| Arquitecto: 8 + 4 años, TOGAF o AWS/Azure SA (6) | Cumple (AWS SA Pro) | **6 años, Cloud Practitioner** | **Azure SA Expert vencida el 31-oct-2026** |
| Especialista en gestión documental (6) | Cumple | **No aportado** | Cumple en el límite |
| ISO 9001 vigente durante la ejecución (7) | Cumple (2028) | **Vencido** (jun-2026) | **Riesgo**: vence en ene-2027, antes de terminar el contrato |
| ISO 27001 vigente (7) | Cumple | **No aporta** | Cumple |
| 15 ingenieros de planta (7) | 32 | **8** | **15 exactos** |
| Ponderables (8) | Experiencia adicional (2 contratos), maestría, ITIL, ISO 20000-1, CMMI 3, industria nacional, discapacidad | Ninguno | Industria nacional mixta (50 puntos) |

## Qué pone a prueba cada escenario

- **A**: el camino feliz, con citas verificables en cada requisito y puntaje ponderable.
- **B**: que se detecten varias brechas habilitantes, se prioricen y se recomiende no presentarse.
- **C**: comparaciones con `≥` y `≤` en valores exactos, valor proporcional en uniones temporales,
  conversión a SMMLV por año de inicio, vigencias que vencen justo antes del cierre o durante la
  ejecución y alcances ambiguos que deben ir a revisión humana en lugar de asumirse.

## Regenerar

```bash
pip install reportlab
python _generador/generar.py .
```

Los datos de cada empresa están en `_generador/escenarios_data.py`.
