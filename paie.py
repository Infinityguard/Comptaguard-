"""Calculs de paie configurables et génération de bulletins PDF (FCFA).
Les paramètres fiscaux/socials doivent être validés pour l'entreprise et la période avant production officielle.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from datetime import date

D = Decimal
CENT = D("1")  # Arrondi au franc CFA entier par défaut.

def dec(value, default="0") -> Decimal:
    if value is None or str(value).strip() == "":
        return D(default)
    s = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return D(s)
    except InvalidOperation as exc:
        raise ValueError(f"Montant invalide : {value}") from exc

def fcfa(value) -> Decimal:
    return dec(value).quantize(CENT, rounding=ROUND_HALF_UP)

def progressive_tax(base, brackets):
    """Calcule un impôt progressif selon une liste (plafond_exclusif, taux).
    Le dernier plafond peut être None. Les taux sont saisis sous forme décimale (0.10 = 10%)."""
    amount = max(D("0"), dec(base))
    lower = D("0")
    tax = D("0")
    for upper, rate in brackets:
        rate = dec(rate)
        if upper is None:
            tax += max(D("0"), amount - lower) * rate
            break
        upper = dec(upper)
        taxable = max(D("0"), min(amount, upper) - lower)
        tax += taxable * rate
        if amount <= upper:
            break
        lower = upper
    return fcfa(tax)

def calculate_payroll(employee: dict, params: dict | None = None) -> dict:
    """Retourne le détail de paie. Les taux sont configurables, aucune retenue légale n'est certifiée."""
    p = params or {}
    profile = str(employee.get("type_salarie", "Mensualisé"))
    base_rate = dec(employee.get("taux_base", employee.get("salaire_base", 0)))
    units = dec(employee.get("unites", 1))
    if profile == "Journalier":
        base_salary = base_rate * units
    elif profile == "Horaire":
        base_salary = base_rate * units
    else:
        base_salary = dec(employee.get("salaire_base", base_rate))
    overtime = dec(employee.get("heures_supp_montant", 0))
    commissions = dec(employee.get("commissions", 0))
    bonus = dec(employee.get("primes", 0))
    allowances = dec(employee.get("indemnites", 0))
    benefits = dec(employee.get("avantages_nature", 0))
    recall = dec(employee.get("rappel_regularisation", 0))
    other_deductions = dec(employee.get("autres_retenues", 0))
    gross_cash = base_salary + overtime + commissions + bonus + allowances + recall
    social_base = max(D("0"), gross_cash + benefits - dec(p.get("exoneration_assiette_sociale", 0)))
    tax_base = max(D("0"), gross_cash + benefits - dec(p.get("abattement_base_imposable", 0)))
    pension_employee_rate = dec(p.get("pension_salarie_taux", "0.036"))
    pension_employer_rate = dec(p.get("pension_employeur_taux", "0.064"))
    family_rate = dec(p.get("prestations_familiales_employeur_taux", "0.09"))
    risk_rate = dec(p.get("risques_professionnels_employeur_taux", "0.02"))
    pension_employee = fcfa(social_base * pension_employee_rate)
    pension_employer = fcfa(social_base * pension_employer_rate)
    family_employer = fcfa(social_base * family_rate)
    risk_employer = fcfa(social_base * risk_rate)
    brackets = p.get("tranches_impot", [(60000, "0"), (150000, "0.10"), (250000, "0.15"), (500000, "0.19"), (None, "0.30")])
    income_tax = progressive_tax(tax_base, brackets) if p.get("activer_impot_progressif", False) else fcfa(tax_base * dec(p.get("impot_salarial_taux", 0)))
    other_withholding = fcfa(dec(p.get("autres_retenues_taux", 0)) * max(D("0"), gross_cash))
    total_deductions = pension_employee + income_tax + other_withholding + other_deductions
    net = gross_cash - total_deductions
    employer_cost = gross_cash + benefits + pension_employer + family_employer + risk_employer
    return {
        "matricule": str(employee.get("matricule", "")), "nom": str(employee.get("nom", "")),
        "prenom": str(employee.get("prenom", "")), "poste": str(employee.get("poste", "")),
        "type_salarie": profile, "periode": str(employee.get("periode", date.today().strftime("%Y-%m"))),
        "salaire_base": fcfa(base_salary), "heures_supp": fcfa(overtime), "commissions": fcfa(commissions),
        "primes": fcfa(bonus), "indemnites": fcfa(allowances), "rappel_regularisation": fcfa(recall),
        "avantages_nature": fcfa(benefits), "brut_cash": fcfa(gross_cash), "base_sociale": fcfa(social_base),
        "base_imposable_parametree": fcfa(tax_base), "pension_salarie": pension_employee,
        "impot_salarial": income_tax, "autres_retenues_taux": other_withholding,
        "autres_retenues": fcfa(other_deductions), "total_retenues": fcfa(total_deductions),
        "net_a_payer": fcfa(net), "pension_employeur": pension_employer,
        "prestations_familiales_employeur": family_employer, "risques_professionnels_employeur": risk_employer,
        "cout_total_employeur": fcfa(employer_cost),
    }

def payslip_pdf(result: dict, employer: dict | None = None) -> bytes:
    """Génère un bulletin PDF simple à partir du calcul déjà réalisé."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("Installe reportlab pour générer le bulletin PDF.") from exc
    employer = employer or {}
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=17*mm, leftMargin=17*mm, topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SmallNote", parent=styles["Normal"], fontSize=8, leading=10))
    story = [Paragraph("BULLETIN DE PAIE — DOCUMENT À VALIDER", styles["Title"]), Spacer(1, 5*mm)]
    story.append(Paragraph(f"<b>Employeur :</b> {employer.get('nom','À renseigner')} &nbsp;&nbsp; <b>IFU :</b> {employer.get('ifu','À renseigner')} &nbsp;&nbsp; <b>CNSS :</b> {employer.get('cnss','À renseigner')}", styles["Normal"]))
    story.append(Paragraph(f"<b>Salarié :</b> {result.get('prenom','')} {result.get('nom','')} &nbsp;&nbsp; <b>Matricule :</b> {result.get('matricule','')} &nbsp;&nbsp; <b>Poste :</b> {result.get('poste','')}", styles["Normal"]))
    story.append(Paragraph(f"<b>Période :</b> {result.get('periode','')} &nbsp;&nbsp; <b>Type :</b> {result.get('type_salarie','')}", styles["Normal"]))
    story.append(Spacer(1, 4*mm))
    def money(x): return f"{dec(x):,.0f}".replace(",", " ") + " FCFA"
    rows = [["Rubrique", "Montant"]]
    labels = [
        ("Salaire de base", "salaire_base"), ("Heures supplémentaires", "heures_supp"), ("Commissions", "commissions"),
        ("Primes", "primes"), ("Indemnités", "indemnites"), ("Rappels / régularisations (+/-)", "rappel_regularisation"),
        ("Avantages en nature (information/assiette)", "avantages_nature"), ("Salaire brut en espèces", "brut_cash"),
        ("Retenue pension salarié", "pension_salarie"), ("Retenue fiscale paramétrée", "impot_salarial"),
        ("Autres retenues calculées", "autres_retenues_taux"), ("Autres retenues saisies", "autres_retenues"),
        ("TOTAL RETENUES", "total_retenues"), ("NET À PAYER", "net_a_payer"),
    ]
    for label, key in labels: rows.append([label, money(result.get(key, 0))])
    table = Table(rows, colWidths=[115*mm, 55*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#183153")), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), .35, colors.grey),
        ("ALIGN", (1,1), (1,-1), "RIGHT"), ("BACKGROUND", (0,-2), (-1,-2), colors.HexColor("#eaf0f6")),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#dcefe3")), ("FONTNAME", (0,-2), (-1,-1), "Helvetica-Bold"),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story.extend([table, Spacer(1, 5*mm), Paragraph("Cotisations patronales indicatives : pension " + money(result.get("pension_employeur",0)) + "; prestations familiales " + money(result.get("prestations_familiales_employeur",0)) + "; risques professionnels " + money(result.get("risques_professionnels_employeur",0)) + ".", styles["SmallNote"]), Spacer(1, 2*mm), Paragraph("ATTENTION : bulletin généré par un outil de préparation. Vérifier les bases, plafonds, exonérations, tranches, taux et mentions obligatoires applicables à la période au Bénin avant remise officielle.", styles["SmallNote"])])
    doc.build(story)
    return buffer.getvalue()
