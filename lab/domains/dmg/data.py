"""Public data: H3K27M tumours from cBioPortal, DMG trials from ClinicalTrials.gov.

Every HTTP response is cached under ``studies/.cache/dmg`` so reruns are
offline and identical. The cache is input data, so its hash is the data version.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CACHE = Path("studies/.cache/dmg")
CBIO = "https://www.cbioportal.org/api"
CTGOV = "https://clinicaltrials.gov/api/v2/studies"

# cohort key -> (cBioPortal study id, has discrete copy-number calls)
COHORTS = {
    "dkfz": ("pediatric_dkfz_2017", False),
    "mskcc": ("glioma_mskcc_2019", True),
    "pipseq": ("mixed_pipseq_2017", False),
    "cptac": ("brain_cptac_2020", True),
    "mai": ("pancan_ped_mai_msk_2025", True),
    "tcga": ("lgggbm_tcga_pub", True),
}
H3 = {3020: "H3.3", 8358: "H3.1", 8352: "H3.1"}  # H3F3A, HIST1H3B, HIST1H3C
K27M = {"K27M", "K28M", "K27I", "K28I"}

# axis -> gene -> what counts (mut = protein-changing, trunc = truncating only, amp, del)
AXES: dict[str, dict[str, set[str]]] = {
    "p53": {"TP53": {"mut", "del"}, "PPM1D": {"trunc", "amp"}, "MDM2": {"amp"}, "MDM4": {"amp"}},
    "acvr1": {"ACVR1": {"mut"}},
    "pi3k": {"PIK3CA": {"mut", "amp"}, "PIK3R1": {"mut"}, "PTEN": {"mut", "del"}, "MTOR": {"mut"}},
    "rtk": {"PDGFRA": {"mut", "amp"}, "EGFR": {"mut", "amp"}, "MET": {"mut", "amp"}, "FGFR1": {"mut", "amp"}, "KIT": {"amp"}},
    "cell_cycle": {"CDKN2A": {"del"}, "CDK4": {"amp"}, "CDK6": {"amp"}, "CCND1": {"amp"}, "CCND2": {"amp"}, "RB1": {"mut", "del"}},
    "mapk": {"BRAF": {"mut"}, "NF1": {"mut", "del"}, "KRAS": {"mut"}, "NRAS": {"mut"}},
}
ENTREZ = {"TP53": 7157, "PPM1D": 8493, "MDM2": 4193, "MDM4": 4194, "ACVR1": 90, "PIK3CA": 5290, "PIK3R1": 5295,
          "PTEN": 5728, "MTOR": 2475, "PDGFRA": 5156, "EGFR": 1956, "MET": 4233, "FGFR1": 2260, "KIT": 3815,
          "CDKN2A": 1029, "CDK4": 1019, "CDK6": 1021, "CCND1": 595, "CCND2": 894, "RB1": 5925, "BRAF": 673,
          "NF1": 4763, "KRAS": 3845, "NRAS": 4893}
SYMBOL = {v: k for k, v in ENTREZ.items()}
SILENT = {"Silent", "3'UTR", "5'UTR", "3'Flank", "5'Flank", "Intron", "IGR", "RNA", "Splice_Region"}
TRUNC = {"Nonsense_Mutation", "Frame_Shift_Del", "Frame_Shift_Ins", "Splice_Site"}
OPEN = {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING"}


def _cached(name: str, fetch: Any) -> Any:
    path = CACHE / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    obj = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return obj


def _http(url: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def cohort(key: str) -> list[dict[str, Any]]:
    """One record per H3K27M patient: variant and the axes it has a hit in."""
    sid, has_cna = COHORTS[key]

    def fetch() -> dict[str, Any]:
        body = {"sampleListId": f"{sid}_all", "entrezGeneIds": list(H3) + list(ENTREZ.values())}
        muts = _http(f"{CBIO}/molecular-profiles/{sid}_mutations/mutations/fetch?projection=SUMMARY", body)
        cna = []
        if has_cna:
            prof = next(p["molecularProfileId"] for p in _http(f"{CBIO}/studies/{sid}/molecular-profiles")
                        if p["molecularAlterationType"] == "COPY_NUMBER_ALTERATION" and p["datatype"] == "DISCRETE")
            cna = _http(f"{CBIO}/molecular-profiles/{prof}/discrete-copy-number/fetch?discreteCopyNumberEventType=HOMDEL_AND_AMP",
                        {"sampleListId": f"{sid}_all", "entrezGeneIds": list(ENTREZ.values())})
        keep = ("patientId", "sampleId", "entrezGeneId", "proteinChange", "mutationType")
        return {"mutations": [{k: m.get(k) for k in keep} for m in muts],
                "cna": [{"patientId": c["patientId"], "sampleId": c["sampleId"], "entrezGeneId": c["entrezGeneId"], "alteration": c["alteration"]} for c in cna]}

    raw = _cached(f"cbio_{sid}", fetch)
    variant: dict[str, str] = {}
    for m in raw["mutations"]:
        if m["entrezGeneId"] in H3 and m["proteinChange"] in K27M:
            variant.setdefault(m["patientId"], H3[m["entrezGeneId"]])
    hits: dict[str, set[str]] = {p: set() for p in variant}
    genes: dict[str, set[str]] = {p: set() for p in variant}
    for m in raw["mutations"]:
        p, sym = m["patientId"], SYMBOL.get(m["entrezGeneId"])
        if p not in hits or not sym or m["mutationType"] in SILENT:
            continue
        kinds = {"mut"} | ({"trunc"} if m["mutationType"] in TRUNC else set())
        for axis, rule in AXES.items():
            if sym in rule and rule[sym] & kinds:
                hits[p].add(axis)
                genes[p].add(f"{sym} {m['proteinChange']}")
    for c in raw["cna"]:
        p, sym = c["patientId"], SYMBOL.get(c["entrezGeneId"])
        if p not in hits or not sym:
            continue
        kind = "amp" if c["alteration"] == 2 else "del" if c["alteration"] == -2 else None
        for axis, rule in AXES.items():
            if sym in rule and kind in rule[sym]:
                hits[p].add(axis)
                genes[p].add(f"{sym} {kind}")
    return [{"cohort": key, "patient": p, "variant": variant[p], "cna": has_cna,
             "axes": sorted(hits[p]), "genes": sorted(genes[p])} for p in sorted(variant)]


TRIAL_QUERIES = {
    "dmg": {"query.cond": "diffuse midline glioma OR DIPG OR diffuse intrinsic pontine glioma OR H3 K27M"},
    "hgg": {"query.cond": "high grade glioma OR glioblastoma OR diffuse midline glioma OR DIPG", "filter.overallStatus": ",".join(sorted(OPEN))},
}


def trials(scope: str = "dmg") -> list[dict[str, Any]]:
    """Trials on ClinicalTrials.gov for one query scope, trimmed to what classification needs."""
    def fetch() -> list[dict[str, Any]]:
        studies, token = [], None
        while True:
            q = urllib.parse.urlencode({**TRIAL_QUERIES[scope], "pageSize": 200, **({"pageToken": token} if token else {}),
                                        "fields": "NCTId,BriefTitle,OverallStatus,StartDate,InterventionName,InterventionType,EligibilityCriteria"})
            page = _http(f"{CTGOV}?{q}")
            studies += page["studies"]
            token = page.get("nextPageToken")
            if not token:
                break
        out = []
        for s in studies:
            ps = s["protocolSection"]
            elig = ps.get("eligibilityModule", {}).get("eligibilityCriteria", "")
            gene_lines = [ln.strip() for ln in elig.splitlines() if any(g in ln for g in (*ENTREZ, "K27M", "H3", "mutation", "alteration", "amplif"))]
            out.append({
                "nct": ps["identificationModule"]["nctId"], "title": ps["identificationModule"]["briefTitle"],
                "status": ps["statusModule"]["overallStatus"], "start": ps["statusModule"].get("startDateStruct", {}).get("date", ""),
                "drugs": [i["name"] for i in ps.get("armsInterventionsModule", {}).get("interventions", []) if i["type"] in ("DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT")],
                "eligibility_genes": " | ".join(gene_lines)[:300],
            })
        return out
    return _cached(f"ctgov_{scope}", fetch)


def version(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]
