"""Fetch the PM-JAY empanelled-hospitals list from the public hospital search.

Source: https://hospitals.pmjay.gov.in/Search/ (National Health Authority) — the
"Registered Hospitals" view has a no-login Excel export (`fn_openExcel` in the page JS).
An all-India export (searchState=-1) returns one .xls with every registered hospital:
State, District, Hospital Type (Public / Private(For Profit) / Private(Not For Profit) /
GOI), Empanelment Type (PMJAY / Only CGHS / State Specific ...), Application Status,
and speciality codes. ~35k rows, ~13 MB, takes 2-4 minutes to generate server-side.

District names are current-vintage (post-2020 districts like Gaurella Pendra Marwahi
and Soreng appear), so the crosswalk needs name-standardisation only, no wholesale
apportionment.

Run:  python fetch_pmjay_hospitals.py
Writes PMJAY_empanelled_hospitals_<date>.xls + _manifest.json next to itself.
If the all-India export times out, falls back to per-state pulls (state IDs are
Census-2011 state codes, enumerated in STATE_IDS) and saves one file per state.
"""

import datetime as dt
import hashlib
import json
import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://hospitals.pmjay.gov.in/Search/empnlWorkFlow.htm"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trilytics-mai-research/1.0"}

# Query-string + form body replicate the page's fn_openExcel() exactly.
QS = ("?actionFlag=ViewRegisteredHosptlsNew&&search=Y&applSearch=N"
      "&appReadOnly=Y&draftMenu=N&invalidMenu=N&export=E")

def form(state_id="-1"):
    return {
        "actionFlag": "ViewRegisteredHosptlsNew", "actionVal": "", "search": "Y",
        "applSearch": "N", "hospInfoId": "", "appReadOnly": "Y", "draftMenu": "N",
        "isRSBY": "", "export": "E", "pageNo": "1",
        "searchState": state_id, "searchDistrict": "-1", "searchHospType": "-1",
        "searchSpeciality": "-1", "searchHospName": "-1", "empanelmentType": "-1",
        "advSearch": "false", "fromDate": "", "toDate": "",
    }

# From the searchState dropdown (Census-2011 state codes; 98/99 are PSU/NHCP pseudo-states).
STATE_IDS = {
    "1": "JAMMU AND KASHMIR", "2": "HIMACHAL PRADESH", "3": "PUNJAB", "4": "CHANDIGARH",
    "5": "UTTARAKHAND", "6": "HARYANA", "7": "NCT OF DELHI", "8": "RAJASTHAN",
    "9": "UTTAR PRADESH", "10": "BIHAR", "11": "SIKKIM", "12": "ARUNACHAL PRADESH",
    "13": "NAGALAND", "14": "MANIPUR", "15": "MIZORAM", "16": "TRIPURA",
    "17": "MEGHALAYA", "18": "ASSAM", "19": "WEST BENGAL", "20": "JHARKHAND",
    "21": "ODISHA", "22": "CHHATTISGARH", "23": "MADHYA PRADESH", "24": "GUJARAT",
    "25": "DAMAN AND DIU", "26": "DADRA AND NAGAR HAVELI", "27": "MAHARASHTRA",
    "28": "ANDHRA PRADESH", "29": "KARNATAKA", "30": "GOA", "31": "LAKSHADWEEP",
    "32": "KERALA", "33": "TAMIL NADU", "34": "PUDUCHERRY",
    "35": "ANDAMAN AND NICOBAR ISLANDS", "36": "TELANGANA", "37": "LADAKH",
    "98": "PSU", "99": "NHCP",
}


def pull(state_id, out_path, timeout=600):
    r = requests.post(BASE + QS, data=form(state_id), headers=UA, timeout=timeout)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")
    if "excel" not in ctype:
        raise RuntimeError(f"unexpected content-type {ctype!r} (site changed / blocked?)")
    with open(out_path, "wb") as f:
        f.write(r.content)
    return len(r.content), hashlib.sha256(r.content).hexdigest()


def main():
    today = dt.date.today().isoformat()
    path = os.path.join(HERE, "_manifest.json")
    # Merge into the existing manifest: rewriting it wholesale dropped the curated method, notes and row counts of
    # the snapshot the pipeline actually reads, and recorded no hash to verify a file by (F-53).
    manifest = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    manifest.setdefault("files", {})
    manifest.update({"source": BASE + QS, "pulled": today})
    all_india = os.path.join(HERE, f"PMJAY_empanelled_hospitals_{today}.xls")
    try:
        size, sha = pull("-1", all_india)
        manifest["files"][os.path.basename(all_india)] = {"state": "ALL-INDIA", "bytes": size, "sha256": sha}
        print(f"all-India export OK: {size:,} bytes")
    except Exception as e:
        print(f"all-India export failed ({e}); falling back to per-state pulls")
        for sid, sname in STATE_IDS.items():
            out = os.path.join(HERE, f"PMJAY_hospitals_state{sid}_{today}.xls")
            size, sha = pull(sid, out)
            manifest["files"][os.path.basename(out)] = {"state": sname, "bytes": size, "sha256": sha}
            print(f"  {sname}: {size:,} bytes")
            time.sleep(5)  # courtesy pause
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("the pipeline reads the 2026-07-16 snapshot by name: point scripts/build_reference.py at a new one "
          "deliberately, then rebuild and re-verify the figures")


if __name__ == "__main__":
    main()
