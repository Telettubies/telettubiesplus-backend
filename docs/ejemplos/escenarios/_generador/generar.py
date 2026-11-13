"""Genera los PDFs, el JSON de base de conocimiento y el resumen de cada escenario."""

import json
import sys
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from escenarios_data import ESCENARIOS, SMMLV

AVISO = "Documento sintético generado para pruebas de software. Entidades, personas, NIT y cifras son ficticios."
ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=15, leading=19, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, leading=15, spaceBefore=10, spaceAfter=4)
P = ParagraphStyle("p", parent=ss["BodyText"], fontSize=9.5, leading=13.5)
SMALL = ParagraphStyle("s", parent=P, fontSize=8, leading=10, textColor=colors.grey, alignment=TA_CENTER)


def cop(v):
    return "$" + f"{v:,.0f}".replace(",", ".")


def num(v, d=2):
    return f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fecha(iso):
    if not iso:
        return "a la fecha"
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    d = date.fromisoformat(iso)
    return f"{d.day} de {meses[d.month - 1]} de {d.year}"


def tabla(filas, anchos=None, encabezado=True):
    t = Table([[Paragraph(str(c), P) for c in f] for f in filas], colWidths=anchos, hAlign="LEFT")
    estilo = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9aa5b1")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if encabezado:
        estilo.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6ebf0")))
    t.setStyle(TableStyle(estilo))
    return t


def kv(pares):
    return tabla([[f"<b>{k}</b>", v] for k, v in pares], [5.2 * cm, 11.3 * cm], encabezado=False)


def construir(ruta, titulo_doc, historia):
    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawString(2 * cm, 1.2 * cm, AVISO)
        canvas.drawRightString(LETTER[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.drawString(2 * cm, LETTER[1] - 1.3 * cm, titulo_doc)
        canvas.restoreState()

    SimpleDocTemplate(str(ruta), pagesize=LETTER, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                      title=titulo_doc, author="Datos sintéticos Criterio").build(historia, onFirstPage=pie, onLaterPages=pie)


def doc_existencia(e, ruta):
    emp = e["empresa"]
    h = [Paragraph("CÁMARA DE COMERCIO (SINTÉTICA)", SMALL), Paragraph("CERTIFICADO DE EXISTENCIA Y REPRESENTACIÓN LEGAL", H1),
         Paragraph(f"Fecha de expedición: {fecha(e['expedicion_certificado'])}", P), Spacer(1, 8),
         kv([("Razón social", emp["razon_social"]), ("NIT", emp["nit"]), ("Domicilio", emp["ciudad"]),
             ("Fecha de constitución", fecha(e["constitucion"])), ("Duración de la sociedad", f"Hasta el {fecha(e['duracion'])}"),
             ("Representante legal", emp["representante_legal"]), ("Objeto social", e["objeto_social"]),
             ("Estado de la matrícula", "Activa, renovada para el año 2026")]),
         Spacer(1, 6), Paragraph("La sociedad no se encuentra disuelta ni en liquidación.", P),
         PageBreak(), Paragraph("DOCUMENTOS JURÍDICOS COMPLEMENTARIOS DEL PROPONENTE", H1)]
    rup = e["rup"]
    h.append(Paragraph("Registro Único de Proponentes (RUP)", H2))
    h.append(kv([("Estado", "Vigente y en firme" if rup["vigente"] else "No vigente"),
                 ("Clasificación UNSPSC (tercer nivel)", ", ".join(rup["codigos"])),
                 ("Información financiera reportada", f"Con corte a 31 de diciembre de {e['financiero']['anio']}")]))
    h.append(Paragraph("Garantía de seriedad de la oferta", H2))
    if e["garantia"]:
        h.append(kv([("Valor asegurado", cop(e["garantia"]["valor"])), ("Vigencia", e["garantia"]["vigencia"]), ("Aseguradora", "Seguros Confianza del Sur S.A. (sintética)")]))
    else:
        h.append(Paragraph("El proponente no aporta póliza de seriedad de la oferta.", P))
    h.append(Paragraph("Certificados y antecedentes", H2))
    h.append(tabla([["Documento", "Resultado"],
                    ["Registro Único Tributario (RUT)", f"Actualizado el {fecha('2026-08-14')}"],
                    ["Antecedentes fiscales (Contraloría)", "No registra antecedentes"],
                    ["Antecedentes disciplinarios (Procuraduría)", "No registra sanciones ni inhabilidades"],
                    ["Antecedentes judiciales del representante legal", "No tiene asuntos pendientes"],
                    ["Registro Nacional de Medidas Correctivas", "No registra medidas correctivas pendientes"],
                    ["Pago de seguridad social y parafiscales (últimos 6 meses)", f"Al día. Firma: {e['parafiscales_firmante']}"]],
                   [8 * cm, 8.5 * cm]))
    construir(ruta, f"{emp['razon_social']} · Existencia, representación y documentos jurídicos", h)


def doc_financiero(e, ruta):
    f, emp = e["financiero"], e["empresa"]
    i = f["indicadores"]
    h = [Paragraph(f"ESTADOS FINANCIEROS CON CORTE A 31 DE DICIEMBRE DE {f['anio']}", H1),
         Paragraph(f"{emp['razon_social']} · NIT {emp['nit']}", P), Paragraph("Cifras en pesos colombianos (COP).", P),
         Paragraph("Estado de situación financiera y de resultados (resumen)", H2),
         tabla([["Cuenta", "Valor (COP)"], ["Activo corriente", cop(f["activo_corriente"])], ["Activo total", cop(f["activo_total"])],
                ["Pasivo corriente", cop(f["pasivo_corriente"])], ["Pasivo total", cop(f["pasivo_total"])], ["Patrimonio", cop(f["patrimonio"])],
                ["Utilidad operacional", cop(f["utilidad_operacional"])], ["Gastos de intereses", cop(f["gastos_intereses"])]], [9 * cm, 7.5 * cm]),
         Paragraph(f"Indicadores financieros y organizacionales {f['anio']}", H2),
         tabla([["Indicador", "Fórmula", "Resultado"],
                ["Índice de liquidez", "Activo corriente / Pasivo corriente", num(i["indice_liquidez"])],
                ["Índice de endeudamiento", "Pasivo total / Activo total", num(i["indice_endeudamiento"])],
                ["Razón de cobertura de intereses", "Utilidad operacional / Gastos de intereses", num(i["razon_cobertura_intereses"])],
                ["Capital de trabajo", "Activo corriente − Pasivo corriente", cop(i["capital_trabajo_cop"])],
                ["Patrimonio", "Patrimonio neto", cop(i["patrimonio_cop"])],
                ["Rentabilidad del patrimonio (ROE)", "Utilidad operacional / Patrimonio", num(i["rentabilidad_patrimonio"], 4)],
                ["Rentabilidad del activo (ROA)", "Utilidad operacional / Activo total", num(i["rentabilidad_activo"], 4)]],
               [5.5 * cm, 6.5 * cm, 4.5 * cm]),
         Spacer(1, 14), Paragraph(f"Firmado: {emp['representante_legal']}, representante legal · {e['parafiscales_firmante']}", P)]
    construir(ruta, f"{emp['razon_social']} · Estados financieros {f['anio']}", h)


def smmlv(c):
    anio = int(c["inicio"][:4])
    return round(c["valor"] / SMMLV[anio], 2) if anio in SMMLV else None


def doc_contratos(e, ruta):
    emp, h = e["empresa"], []
    for n, c in enumerate(e["contratos"]):
        if n:
            h.append(PageBreak())
        filas = [("Contratante", f"{c['entidad']} ({'entidad pública' if c['publica'] else 'entidad privada'})"),
                 ("Contratista", f"{emp['razon_social']} · NIT {emp['nit']}"), ("Contrato No.", c["id"]), ("Objeto", c["objeto"]),
                 ("Fecha de inicio", fecha(c["inicio"])), ("Fecha de terminación", fecha(c["fin"])), ("Valor total ejecutado", cop(c["valor"])),
                 ("Porcentaje de participación", f"{c['part']} %"), ("Códigos UNSPSC", ", ".join(c["unspsc"]))]
        s = smmlv(c)
        if s:
            filas.append(("Valor en SMMLV del año de inicio", num(s)))
        if "ans" in c:
            filas.append(("Cumplimiento de acuerdos de nivel de servicio (ANS)", f"{num(c['ans'], 1)} %"))
        if c.get("migrados"):
            filas.append(("Documentos o registros migrados", f"{c['migrados']:,}".replace(",", ".")))
        filas.append(("Estado", "Ejecutado, terminado y liquidado a satisfacción"))
        if c.get("extra"):
            filas.append(("Observaciones", c["extra"]))
        h += [Paragraph(c["entidad"].upper(), SMALL), Paragraph("CERTIFICACIÓN DE EXPERIENCIA Y CUMPLIMIENTO CONTRACTUAL", H1),
              Paragraph("El suscrito supervisor certifica que el contratista ejecutó el siguiente contrato:", P), Spacer(1, 6), kv(filas),
              Spacer(1, 12), Paragraph(f"Se expide a solicitud del interesado el {fecha(e['expedicion_certificado'])}.", P)]
    construir(ruta, f"{emp['razon_social']} · Certificaciones de experiencia", h)


def doc_personas(e, ruta):
    emp, h = e["empresa"], []
    for n, p in enumerate(e["personas"]):
        if n:
            h.append(PageBreak())
        h += [Paragraph("HOJA DE VIDA", H1), kv([("Nombre", p["nombre"]), ("Cargo propuesto", p["cargo"]), ("Profesión", p["profesion"]),
                                                ("Años de experiencia profesional general", str(p["anios"])), ("Empresa proponente", emp["razon_social"])]),
              Paragraph("Formación académica", H2),
              tabla([["Nivel", "Título", "Institución", "Fecha de grado"]] + [[t[0].capitalize(), t[1], t[2], fecha(t[4])] for t in p["titulos"]],
                    [2.8 * cm, 5.2 * cm, 5 * cm, 3.5 * cm]),
              Paragraph("Experiencia profesional", H2),
              tabla([["Cargo", "Entidad", "Desde", "Hasta", "Funciones"]] + [[x[0], x[1], fecha(x[2]), fecha(x[3]), x[4]] for x in p["experiencias"]],
                    [3.3 * cm, 3.3 * cm, 2.7 * cm, 2.7 * cm, 4.5 * cm]),
              Paragraph("Certificaciones", H2)]
        h.append(tabla([["Certificación"]] + [[c] for c in p["certs"]], [16.5 * cm]) if p["certs"] else Paragraph("No acredita certificaciones.", P))
    construir(ruta, f"{emp['razon_social']} · Hojas de vida del equipo", h)


def doc_certificaciones(e, ruta):
    emp, h = e["empresa"], []
    for n, c in enumerate(e["certificaciones"]):
        if n:
            h.append(PageBreak())
        h += [Paragraph(c["entidad_emisora"].upper(), SMALL), Paragraph("CERTIFICADO DE SISTEMA DE GESTIÓN", H1),
              Paragraph(f"Se certifica que <b>{emp['razon_social']}</b> (NIT {emp['nit']}) cumple los requisitos de la norma:", P), Spacer(1, 6),
              kv([("Norma / modelo", c["nombre"]), ("Entidad emisora", c["entidad_emisora"]), ("Fecha de expedición", fecha(c["fecha_expedicion"])),
                  ("Fecha de vencimiento", fecha(c["fecha_vencimiento"])), ("Alcance", c["alcance"])])]
    h += [PageBreak(), Paragraph("CERTIFICACIÓN DE CAPACIDADES DEL PROPONENTE", H1),
          Paragraph(f"El representante legal de {emp['razon_social']}, {emp['representante_legal']}, certifica bajo la gravedad de juramento:", P), Spacer(1, 6),
          tabla([["Capacidad", "Descripción"]] + [[k, v] for k, v in e["capacidades"]], [5 * cm, 11.5 * cm])]
    construir(ruta, f"{emp['razon_social']} · Certificaciones y capacidades", h)


def base_json(e):
    return {
        "empresa": e["empresa"],
        "financiero": [e["financiero"]["indicadores"]],
        "contratos": [{"id": c["id"], "objeto": c["objeto"], "entidad_contratante": f"{c['entidad']} ({'pública' if c['publica'] else 'privada'})",
                       "fecha_inicio": c["inicio"], "fecha_fin": c["fin"], "valor_cop": c["valor"], "valor_smmlv": smmlv(c),
                       "codigos_unspsc": c["unspsc"], "porcentaje_participacion": c["part"],
                       "descripcion": " ".join(x for x in [f"Cumplimiento de ANS: {num(c['ans'], 1)} %." if "ans" in c else "",
                                                          f"Documentos migrados: {c['migrados']:,}.".replace(",", ".") if c.get("migrados") else "",
                                                          c.get("extra", "")] if x) or None} for c in e["contratos"]],
        "personas": [{"id": p["id"], "nombre": p["nombre"], "profesion": p["profesion"], "anios_experiencia_general": p["anios"],
                      "titulos": [{"nivel": t[0], "titulo": t[1], "institucion": t[2], "anio": t[3]} for t in p["titulos"]],
                      "experiencias": [{"cargo": x[0], "entidad": x[1], "fecha_inicio": x[2], "fecha_fin": x[3], "descripcion": x[4]} for x in p["experiencias"]],
                      "certificaciones": p["certs"]} for p in e["personas"]],
        "certificaciones": e["certificaciones"],
        "capacidades": [{"nombre": k, "descripcion": v} for k, v in e["capacidades"]] + [
            {"nombre": "Duración de la sociedad", "descripcion": f"Constituida el {e['constitucion']}; duración hasta el {e['duracion']}. Certificado de existencia expedido el {e['expedicion_certificado']}."},
            {"nombre": "Registro Único de Proponentes", "descripcion": f"{'Vigente y en firme' if e['rup']['vigente'] else 'No vigente'}; códigos UNSPSC: {', '.join(e['rup']['codigos'])}."},
            {"nombre": "Garantía de seriedad de la oferta", "descripcion": f"{cop(e['garantia']['valor'])}, vigencia {e['garantia']['vigencia']}." if e["garantia"] else "No se aporta póliza de seriedad."},
            {"nombre": "Seguridad social y parafiscales", "descripcion": f"Al día; certificación firmada por: {e['parafiscales_firmante']}."},
        ],
    }


def main(salida):
    salida = Path(salida)
    for e in ESCENARIOS:
        d = salida / e["carpeta"]
        d.mkdir(parents=True, exist_ok=True)
        doc_existencia(e, d / "01_certificado_existencia_y_documentos_juridicos.pdf")
        doc_financiero(e, d / "02_estados_financieros_2025.pdf")
        doc_contratos(e, d / "03_certificaciones_experiencia.pdf")
        doc_personas(e, d / "04_hojas_de_vida.pdf")
        doc_certificaciones(e, d / "05_certificaciones_y_capacidades.pdf")
        (d / "base_conocimiento.json").write_text(json.dumps(base_json(e), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
