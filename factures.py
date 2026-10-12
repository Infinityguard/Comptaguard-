"""Contrôles de cohérence sur factures normalisées et TVA, sans authentification officielle."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import pandas as pd
from io import BytesIO

ALIASES = {
 "numero_facture": ["numero_facture", "n° facture", "no facture", "numéro facture", "numero facture", "référence facture", "reference facture"],
 "date_facture": ["date_facture", "date facture", "date"],
 "fournisseur": ["fournisseur", "nom fournisseur", "vendeur", "client/fournisseur"],
 "ifu": ["ifu", "ifu fournisseur", "identifiant fiscal unique"],
 "nim": ["nim", "n° nim", "numero nim"],
 "code_mecef": ["code_mecef", "code mecef", "code signature", "signature électronique", "signature electronique", "code facture"],
 "base_ht": ["base_ht", "ht", "montant ht", "montant hors taxe", "base taxable"],
 "tva_declaree": ["tva_declaree", "tva", "montant tva", "montant taxe"],
 "total_ttc": ["total_ttc", "ttc", "montant ttc", "total toutes taxes"],
 "taux_tva": ["taux_tva", "taux tva", "tva taux"],
}
def _d(v):
 if pd.isna(v) or str(v).strip()=="": return None
 s=str(v).strip().replace(" ","").replace("\u00a0","")
 if "," in s and "." in s: s=s.replace(".","").replace(",",".") if s.rfind(",")>s.rfind(".") else s.replace(",","")
 else: s=s.replace(",",".")
 try: return Decimal(s)
 except InvalidOperation: return None

def normalize_invoice_columns(df):
 out=df.copy(); norm={" ".join(str(c).lower().replace("_"," ").split()):c for c in out.columns}; rename={}
 for target, aliases in ALIASES.items():
  for alias in aliases:
   key=" ".join(alias.lower().replace("_"," ").split())
   if key in norm: rename[norm[key]]=target; break
 out=out.rename(columns=rename)
 for col in ALIASES:
  if col not in out: out[col]=""
 return out

def check_invoices(df, vat_rate="0.18", tolerance="1"):
 data=normalize_invoice_columns(df); rate_default=_d(vat_rate) or Decimal("0.18"); tol=_d(tolerance) or Decimal("1")
 results=[]; seen=set()
 for idx,row in data.iterrows():
  issues=[]; num=str(row["numero_facture"]).strip(); ifu=str(row["ifu"]).strip(); nim=str(row["nim"]).strip(); code=str(row["code_mecef"]).strip()
  if not num: issues.append("Numéro de facture absent")
  if num and num in seen: issues.append("Numéro de facture répété dans le fichier")
  if num: seen.add(num)
  if not ifu: issues.append("IFU absent — vérifier le justificatif")
  if not nim: issues.append("NIM absent — vérifier selon le type de facture et les règles applicables")
  if not code: issues.append("Code/signature électronique absent — vérifier selon le type de facture")
  ht=_d(row["base_ht"]); tva=_d(row["tva_declaree"]); ttc=_d(row["total_ttc"]); rate=_d(row["taux_tva"])
  if rate is None: rate=rate_default
  if ht is None: issues.append("Base HT absente ou non numérique")
  if tva is None: issues.append("TVA absente ou non numérique")
  if ttc is None: issues.append("Total TTC absent ou non numérique")
  expected_vat=(ht*rate).quantize(Decimal("1"),rounding=ROUND_HALF_UP) if ht is not None else None
  expected_ttc=ht+tva if ht is not None and tva is not None else None
  if ht is not None and tva is not None and expected_vat is not None and abs(tva-expected_vat)>tol: issues.append("TVA déclarée différente de la TVA calculée au taux saisi")
  if expected_ttc is not None and ttc is not None and abs(ttc-expected_ttc)>tol: issues.append("TTC différent de HT + TVA")
  results.append({"numero_facture":num,"date_facture":row["date_facture"],"fournisseur":row["fournisseur"],"ifu":ifu,"nim":nim,"code_mecef":code,"base_ht":str(ht) if ht is not None else "","tva_declaree":str(tva) if tva is not None else "","tva_calculee_indicative":str(expected_vat) if expected_vat is not None else "","total_ttc":str(ttc) if ttc is not None else "","anomalies":"; ".join(issues),"statut":"À vérifier" if issues else "Cohérence arithmétique OK","ligne_source":int(idx)+2})
 report=pd.DataFrame(results)
 summary={"factures_controlees":len(report),"factures_a_verifier":int((report["statut"]=="À vérifier").sum()) if not report.empty else 0,"factures_sans_anomalie_detectee":int((report["statut"]!="À vérifier").sum()) if not report.empty else 0}
 return report,summary

def invoice_template_bytes():
 sample=pd.DataFrame([{"numero_facture":"EXEMPLE-001","date_facture":"2026-01-15","fournisseur":"Remplacer par le fournisseur","ifu":"À renseigner","nim":"À renseigner selon la facture","code_mecef":"À renseigner si applicable","base_ht":100000,"tva_declaree":18000,"total_ttc":118000,"taux_tva":0.18}])
 buf=BytesIO()
 with pd.ExcelWriter(buf,engine="openpyxl") as writer:
  sample.to_excel(writer,index=False,sheet_name="Factures")
  pd.DataFrame([{"Note":"Exemple fictif. Le taux de TVA est paramétrable. La présence de champs ne prouve pas l'authenticité; vérifier auprès de la source officielle."}]).to_excel(writer,index=False,sheet_name="Instructions")
 return buf.getvalue()
