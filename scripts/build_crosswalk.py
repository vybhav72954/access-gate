#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_crosswalk.py  --  Forward-crosswalk our three assigned datasets onto the LGD frame.

Our procurement scope (Standard catalog, last 3 rows):
  1. data_gov_in/                -- RHS district health-infrastructure panel (2006-2019)
  2. medical_education_tertiary/ -- NMC UG (MBBS) + PG seat matrices 2024-25 (PG placed by
                                    college-name join to UG, town fallback, tier-2 override)
  3. economic_financial_access/  -- RBI Bank Outlets, RBI Statement 4A (deposits/credit/CD),
                                    NABARD-WB (single-state, supplementary; SLBC-MP retired, superseded by 4A)

None of these carry a Census/LGD code -- they are keyed by free-text (state, district) name.
This script standardises every source name to the current LGD `district_code` (785-district
frame) via a curated alias dictionary + fuzzy fallback, then forward-apportions any old
undivided district onto its current child districts by 2011 population (from SHRUG view3).

Design mirrors notebook/build_views.py. Reads data/raw/ + the LGD x SHRUG backbone views in
data/processed/district_frame/ (view2, view3); writes all our outputs to data/processed/crosswalk/.
Dependencies: pandas, numpy, rapidfuzz, openpyxl.

Outputs (data/processed/crosswalk/):
  consolidated_by_district.csv        CONSOLIDATED 785-row wide table (headline vars, all sources)
  rhs_health_panel.csv                RHS facilities, long: district_code x year (+ is_apportioned)
  rhs_health_2019.csv                 RHS latest snapshot, wide (SC/PHC incl. HWC combined)
  nmc_ug_by_district.csv              MBBS colleges + seats per current district
  nmc_pg_by_district.csv              PG (MD/MS/DM/MCh) seats + super-specialty seats per district
  rbi_bank_outlets.csv                bank outlets per current district (by group + pop-group)
  rbi_deposits_credit_panel.csv       Statement 4A, long: district_code x period
  rbi_deposits_credit_latest.csv      Statement 4A latest + 3y CAGR + CD ratio + per-capita
  nabard_wb.csv                       supplementary single-state, LGD-keyed (SLBC-MP retired, superseded by 4A)
  name_map.csv                        reusable (source, state, name) -> LGD code gazetteer
  unmatched_report.csv                every unresolved / ambiguous row, with best guess + value
  nmc_manual_placements.csv           tier-2 hand-placed NMC UG colleges (audit trail)
  nmc_pg_manual_placements.csv        tier-2 hand-placed NMC PG-only institutes (audit trail)

Reusable engine (importable, no side effects on import): `from build_crosswalk import crosswalk_file`
-- one call puts ANY district-keyed file onto the 785-LGD frame. See its docstring for usage.
"""
import os, re, warnings
import numpy as np
import pandas as pd
from rapidfuzz import process, fuzz
warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW  = os.path.join(ROOT, "data", "raw")
PROC = os.path.join(ROOT, "data", "processed")
FRAME = os.path.join(PROC, "district_frame")   # read: Pranav's LGD x SHRUG backbone (views)
CW    = os.path.join(PROC, "crosswalk")        # write: our forward-crosswalk outputs
os.makedirs(CW, exist_ok=True)

# ======================================================================================
# 1 | Name normalisation + curated alias dictionaries
# ======================================================================================
def norm(s):
    s = str(s).strip().lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"\s+", " ", s)

STATE_ALIAS = {
    "a and n islands": "andaman and nicobar islands",
    "andaman and nicobar": "andaman and nicobar islands",
    "orissa": "odisha", "pondicherry": "puducherry", "uttaranchal": "uttarakhand",
    "chattisgarh": "chhattisgarh", "chhatisgarh": "chhattisgarh",
    "jammu and kashmir and ladakh": "jammu and kashmir",
    "dadra and nagar haveli": "the dadra and nagar haveli and daman and diu",
    "daman and diu": "the dadra and nagar haveli and daman and diu",
    "dadra and nagar haveli and daman and diu": "the dadra and nagar haveli and daman and diu",
    "d and n haveli": "the dadra and nagar haveli and daman and diu",
    "nct of delhi": "delhi", "telengana": "telangana",
    "a and n island": "andaman and nicobar islands",
}
def norm_state(s):
    n = norm(s); return STATE_ALIAS.get(n, n)

def _decamel(s):
    """Split run-together camelCase / letter-digit names into words:
    'SouthTwentyFourParganas' -> 'South Twenty Four Parganas',
    'North24Parganas' -> 'North 24 Parganas', 'KarbiAnglong' -> 'Karbi Anglong'."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", s)
    s = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", s)
    return s

# (state_norm, src_norm) -> target_lgd_norm   [names that collide across states]
STATE_SCOPED = {
    ("karnataka", "bijapur"): "vijayapura",
    ("maharashtra", "aurangabad"): "chhatrapati sambhajinagar",
    ("chhattisgarh", "balrampur"): "balrampur ramanujganj",
    ("delhi", "west delhi"): "west", ("delhi", "north delhi"): "north",
    ("delhi", "central delhi"): "central", ("delhi", "south delhi"): "south",
    ("delhi", "east delhi"): "east", ("delhi", "north west delhi"): "north west",
    ("delhi", "north east delhi"): "north east", ("delhi", "south west delhi"): "south west",
    ("delhi", "south east delhi"): "south east", ("delhi", "new delhi"): "new delhi",
    ("delhi", "shahdara"): "shahdara",
    ("tripura", "north"): "north tripura", ("tripura", "south"): "south tripura",
    ("tripura", "west"): "west tripura",
    # Sikkim's 2011 districts are generic directions ("East District" ...) that otherwise
    # collide with Delhi's East/North/South/West -> pin them to Sikkim's LGD names.
    # (2021 carve-outs Pakyong<-East, Soreng<-West inherit from these.)
    ("sikkim", "east"): "gangtok", ("sikkim", "north"): "mangan",
    ("sikkim", "south"): "namchi", ("sikkim", "west"): "gyalshing",
    # Maharashtra spells Raigad as "Raigarh", colliding with Chhattisgarh's Raigarh.
    ("maharashtra", "raigarh"): "raigad",
    # Vav-Tharad (Gujarat, Jan-2025, carved from Banaskantha) is not in the LGD frame yet;
    # fold its rows back into the parent so its RBI 4A money is not dropped (Rs 2,419 cr).
    ("gujarat", "vav tharad"): "banas kantha",
    # HMIS 2017-19 district-name variants (Brihan Mumbai is pop-split in harmonise_hmis, not here).
    ("punjab", "mohali sas nagar"): "s a s nagar",
    ("telangana", "komaram bheem"): "kumuram bheem asifabad",
    ("telangana", "yadadri bhonagiri"): "yadadri bhuvanagiri",
    # geoBoundaries-2021 ADM2 variants (OSM POI pull). "Warangal (U)" is Hanumakonda's
    # pre-2021-rename name and MUST be pinned: unaliased it fuzzy-lands on Warangal,
    # double-counting Warangal and zeroing Hanumakonda.
    ("telangana", "warangal u"): "hanumakonda",
    ("telangana", "warangal r"): "warangal",
    ("telangana", "bhadradri"): "bhadradri kothagudem",
    ("telangana", "jayashankar"): "jayashankar bhupalapally",
    ("telangana", "yadadri bhongiri"): "yadadri bhuvanagiri",
    ("telangana", "jogulamba"): "jogulamba gadwal",
    ("tripura", "sipahijula"): "sepahijala", ("tripura", "sipahijala"): "sepahijala",
    ("tripura", "unokoti"): "unakoti",
    ("assam", "karbi anglong east"): "karbi anglong",
    ("gujarat", "batod"): "botad",
    # PM-JAY hospital-portal variants (Tier-1 #2 pull). Sikkim's renamed districts keep
    # their pre-2021 names in the portal WHILE the carved-out children (Pakyong, Soreng)
    # have their own rows -> plain rename aliases, no apportionment wanted.
    ("andhra pradesh", "ananthpur"): "ananthapuramu",
    ("andhra pradesh", "nellor"): "sri potti sriramulu nellore",
    ("assam", "north cachar hill"): "dima hasao",
    ("jammu and kashmir", "bandipur"): "bandipora",
    ("sikkim", "east sikkim"): "gangtok",
    ("sikkim", "north sikkim"): "mangan",
    ("sikkim", "south sikkim"): "namchi",
    ("sikkim", "west sikkim"): "gyalshing",
    ("uttar pradesh", "raebareli"): "rae bareli",
    ("west bengal", "east midnapur"): "purba medinipur",
    ("west bengal", "west midnapur"): "paschim medinipur",
}
# src_norm -> target_lgd_norm  [nationally-unique renames / spellings / city->district]
GLOBAL = {
    # Karnataka
    "bangalore": "bengaluru urban", "bangalore u": "bengaluru urban",
    "bangalore r": "bengaluru rural", "bangalore rural": "bengaluru rural",
    # "Bengaluru South" = RAMANAGARA renamed (Karnataka, 2024) -- the only sources using it
    # (RBI outlets + 4A 2024+) mean the renamed district, never a part of Bengaluru Urban,
    # whose own row appears separately as "BENGALURU URBAN" in both.
    "bengaluru": "bengaluru urban", "bengaluru south": "ramanagara",
    "bengaluru north": "bengaluru rural", "bangalore urban": "bengaluru urban",
    "mangalore": "dakshina kannada", "mysore": "mysuru", "gulbarga": "kalaburagi",
    "shimoga": "shivamogga", "bellary": "ballari", "belgaum": "belagavi",
    "tumkur": "tumakuru", "hubli": "dharwad", "hubli dharwad": "dharwad",
    "hubballi": "dharwad", "mangaluru": "dakshina kannada", "manipal": "udupi",
    "udipi": "udupi", "chikmagalur": "chikkamagaluru", "chamrajnagar": "chamarajanagara",
    "bagalkot": "bagalkote", "bijapur karnataka": "vijayapura",
    "chikballapur": "chikkaballapura", "chikkaballapur": "chikkaballapura",
    "chikballapura": "chikkaballapura", "chikkamagal": "chikkamagaluru",
    "karwar": "uttara kannada", "sullia": "dakshina kannada",
    # West Bengal
    "hugli": "hooghly", "haora": "howrah", "darjiling": "darjeeling",
    "koch bihar": "cooch behar", "coochbehar": "cooch behar", "kooch bihar": "cooch behar",
    "burdwan": "purba bardhaman", "bardhaman": "purba bardhaman",
    "barddhaman": "purba bardhaman", "durgapur": "paschim bardhaman",
    "west dinajpur": "uttar dinajpur", "medinipur w": "paschim medinipur",
    "medinipur e": "purba medinipur", "west medinipur": "paschim medinipur",
    "east medinipur": "purba medinipur", "purba midanpore": "purba medinipur",
    "paschim midnapore": "paschim medinipur", "west midnapore": "paschim medinipur",
    "midnapore": "paschim medinipur",
    "north twenty four parganas": "north 24 parganas",
    "south twenty four parganas": "south 24 parganas",
    "twenty four parganas north": "north 24 parganas",
    "twenty four parganas south": "south 24 parganas",
    "kalyani": "nadia", "jadavpur": "kolkata", "kamarhati": "north 24 parganas",
    "raiganj": "uttar dinajpur", "rampurhat": "birbhum",
    # Odisha
    "khurda": "khordha", "keonjhar": "kendujhar", "nawrangpur": "nabarangpur",
    "nabrangapur": "nabarangpur", "nawapara": "nuapada", "bhubaneswar": "khordha",
    "bhubaneswa": "khordha", "berhampur": "ganjam", "baudh": "boudh",
    "debagarh": "deogarh", "balasore": "baleshwar", "baleswar": "baleshwar",
    "jagatsinghpur": "jagatsinghapur", "sonapur": "sonepur", "subarnapur": "sonepur",
    "angul": "anugul", "bolangir": "balangir", "burla": "sambalpur",
    "baripada": "mayurbhanj", "rourkela": "sundargarh",
    # Andhra Pradesh
    "kadapa": "y s r", "cuddapah": "y s r", "ysr kadapa": "y s r", "ysr": "y s r",
    "anantapur": "ananthapuramu", "ananthapur": "ananthapuramu",
    "nellore": "sri potti sriramulu nellore", "spsr nellore": "sri potti sriramulu nellore",
    "markapuram": "prakasam", "ntr district": "ntr", "rajahmundry": "east godavari",
    "ongole": "prakasam", "vijaywada": "ntr", "vijayawada": "ntr",
    "kuppam": "chittoor", "mangalagiri": "guntur",
    "amalapuram": "dr b r ambedkar konaseema",
    # Telangana
    "medchal": "medchal malkajgiri", "rangareddi": "ranga reddy",
    "rangareddy": "ranga reddy", "k v rangareddy": "ranga reddy",
    # Warangal Urban (2016-2021) was RENAMED Hanumakonda in 2021 -- it is the successor
    # district holding the tri-city urban core, never a part of current Warangal (which
    # succeeds Warangal Rural). Folding Urban into Warangal double-counts Warangal and
    # zeroes Hanumakonda across RHS-2017/2019, HMIS, NFHS-5 and PM-JAY.
    "warangal urban": "hanumakonda", "warangal u": "hanumakonda",
    "warangal rural": "warangal", "warangal r": "warangal", "mahbubnagar": "mahabubnagar",
    "mehboobnagar": "mahabubnagar", "secunderabad": "hyderabad", "sanath nagar": "hyderabad",
    "narketpally": "nalgonda", "patancheru": "sangareddy", "ramagundam": "peddapalli",
    "jogulumba": "jogulamba gadwal", "jangaon": "jangoan",
    "yadadri bhongir": "yadadri bhuvanagiri", "yadadri": "yadadri bhuvanagiri",
    "bhongir": "yadadri bhuvanagiri",
    # Jharkhand
    "purbi singhbhum": "east singhbum", "e singhbhum": "east singhbum",
    "east singhbhum": "east singhbum", "purbi singhbum": "east singhbum",
    "paschimi singhbhum": "west singhbhum", "pashchimi singhbhum": "west singhbhum",
    "w singhbhum": "west singhbhum", "west singhbum": "west singhbhum",
    "saraikela": "saraikela kharsawan", "seraikela": "saraikela kharsawan",
    "kodarma": "koderma", "hazaribag": "hazaribagh", "sahibganj": "sahebganj",
    "jamshedpur": "east singhbum", "jameshedpur": "east singhbum",
    "bishrampur": "palamu", "dighi dumka": "dumka",
    # Madhya Pradesh
    "west nimar": "khargone west nimar", "east nimar": "khandwa east nimar",
    "khargone": "khargone west nimar", "khandwa": "khandwa east nimar",
    "hoshangabad": "narmadapuram", "narsinghpur": "narsimhapur",
    "singroli": "singrauli", "sigrouli": "singrauli", "badwani": "barwani",
    "chhindwada": "chhindwara",  # HMIS-portal 2018-19 spells it "Chhindwada"
    "mandsour": "mandsaur", "bairagarh": "bhopal", "mowa": "raipur",
    # Assam
    "kamrup m": "kamrup metro", "kamrup metropolitan": "kamrup metro",
    "kamrup r": "kamrup", "kamrup rural": "kamrup", "sibsagar": "sivasagar",
    "sribhumi": "karimganj", "n c hills": "dima hasao", "guwahati": "kamrup metro",
    "morigaon": "marigaon", "silchar": "cachar", "tezpur": "sonitpur",
    "north lakhimpur": "lakhimpur", "diphu": "karbi anglong",
    # Bihar
    "e champaran": "purbi champaran", "east champaran": "purbi champaran",
    "w champaran": "pashchim champaran", "west champaran": "pashchim champaran",
    "kaimur": "kaimur bhabua", "sasaram": "rohtas", "purnea": "purnia",
    "jahanabad": "jehanabad", "lehriasarai": "darbhanga", "bettiah": "pashchim champaran",
    "buxer": "buxar",
    # Andaman & Nicobar
    "nicobar": "nicobars",
    # Haryana
    "ch dadri": "charki dadri", "charkhi dadri": "charki dadri", "gurgaon": "gurugram",
    "mahendragarh narnaul": "mahendragarh", "narnaul": "mahendragarh", "mewat": "nuh",
    "hansi": "hisar", "mohidergarh": "mahendragarh", "nalhar": "nuh",
    "sadopur": "ambala", "agroha": "hisar", "sonepat": "sonipat",
    # Gujarat
    "dohad": "dahod", "kutchh": "kachchh", "kutch": "kachchh", "kutch nhuj": "kachchh",
    "mehsana": "mahesana", "panchmahal": "panch mahals", "panchmahals": "panch mahals",
    "sabarkantha": "sabar kantha", "banaskantha": "banas kantha", "the dangs": "dangs",
    "dang": "dangs", "baroda": "vadodara", "vadodra": "vadodara",
    "chhota udepur": "chhotaudepur", "chhota udaipur": "chhotaudepur",
    "aravalli": "arvalli", "arvali": "arvalli", "aravali": "arvalli",
    "ahmadabad": "ahmedabad", "nadiad": "kheda", "karmsad": "anand",
    "himmatnagar": "sabar kantha", "palanpur": "banas kantha", "dharpur patan": "patan",
    "sola": "ahmedabad", "gotri": "vadodara", "bhuj": "kachchh",
    "surendra nagar": "surendranagar", "rajpipla": "narmada",
    # Rajasthan
    "chittaurgarh": "chittorgarh", "dhaulpur": "dholpur", "salumber": "salumbar",
    "sri ganganagar": "ganganagar", "sri gangana": "ganganagar", "jagatpura": "jaipur",
    # Ladakh / J&K
    "leh": "leh ladakh", "badgam": "budgam", "rajauri": "rajouri",
    "bandipore": "bandipora", "vijaypur": "samba",
    # Himachal
    "l and s": "lahaul and spiti", "lahul and spiti": "lahaul and spiti",
    "kulu": "kullu", "sirmour": "sirmaur",
    # Maharashtra
    "osmanabad": "dharashiv", "ahmadnagar": "ahmednagar", "ahilyanagar": "ahmednagar",
    "bid": "beed", "karad": "satara", "gondiya": "gondia",
    "greater mumbai": "mumbai", "mumbai suburb": "mumbai suburban",
    "loni": "ahmednagar", "miraj": "sangli", "juhu": "mumbai suburban",
    "ambajogai": "beed", "ambernath": "thane", "vashi": "thane",
    "alibag": "raigad", "baramati": "pune",
    # Tamil Nadu
    "nilgiris": "the nilgiris", "trichy": "tiruchirappalli",
    "tiruchirapalli": "tiruchirappalli", "kanchipuram": "kancheepuram",
    "tuticorin": "thoothukkudi", "thoothukudi": "thoothukkudi",
    "villupuram": "viluppuram", "tirupur": "tiruppur",
    "enathur": "kancheepuram", "maduranthagam": "chengalpattu",
    "asaripallam": "kanniyakumari", "hosur": "krishnagiri",
    "padukottai": "pudukkottai", "melmaruvathur": "chengalpattu",
    "annamalainagar": "cuddalore", "perundurai": "erode", "omandurar": "chennai",
    # Kerala
    "calicut": "kozhikode", "trivandrum": "thiruvananthapuram",
    "thiruvanant hapuram": "thiruvananthapuram", "palghat": "palakkad",
    "alleppey": "alappuzha", "cannanore": "kannur", "quilon": "kollam",
    "trichur": "thrissur", "kochi": "ernakulam", "perinthalmanna": "malappuram",
    "thodupuzha": "idukki", "thiruvalla": "pathanamthitta", "tiruvalla": "pathanamthitta",
    "yakkara": "palakkad", "kolenchery": "ernakulam", "konni": "pathanamthitta",
    # Uttar Pradesh
    "allahabad": "prayagraj", "kanpur": "kanpur nagar", "barabanki": "bara banki",
    "sant ravidas nagar": "bhadohi", "noida": "gautam buddha nagar",
    "rai bareli": "rae bareli", "raibareli": "rae bareli", "raibareilly": "rae bareli",
    "kushi nagar": "kushinagar", "maharajganj": "mahrajganj", "maharganj": "mahrajganj",
    "maharanganj": "mahrajganj", "shravasti": "shrawasti", "faizabad": "ayodhya",
    "jyotiba phule nagar": "amroha", "jyotibha phule nagar": "amroha",
    "badaun": "budaun", "aurayya": "auraiya", "chaundoli": "chandauli",
    "chandoli": "chandauli", "ferozabad": "firozabad",
    "gautam b nagar": "gautam buddha nagar", "gautambudhnagar": "gautam buddha nagar",
    "santkabirnagar": "sant kabir nagar", "unnav": "unnao", "maunathbhanjan": "mau",
    "kashi ram nagar": "kasganj", "kanshiram nagar": "kasganj",
    "khiri": "kheri", "lakhimpur kheri": "kheri", "lakhimpur khiri": "kheri",
    "siddharth nagar": "siddharthnagar", "sidharthanagar": "siddharthnagar",
    "jp nagar": "amroha", "j p nagar": "amroha", "csm nagar": "amethi",
    "c s m nagar": "amethi",
    # Uttarakhand
    "garhwal": "pauri garhwal", "pauri": "pauri garhwal", "tehri": "tehri garhwal",
    "udham singh nagar": "udam singh nagar", "udhamsingh nagar": "udam singh nagar",
    "udhamsingh": "udam singh nagar", "u s nagar": "udam singh nagar",
    "rudraprayag": "rudra prayag", "uttarkashi": "uttar kashi", "hardwar": "haridwar",
    "rishikesh": "dehradun", "haldwani": "nainital", "dehradum": "dehradun",
    # Punjab
    "muktsar": "sri muktsar sahib", "mukatsar": "sri muktsar sahib",
    "sahibzada ajit singh nagar": "s a s nagar", "mohali": "s a s nagar",
    "sas nagar": "s a s nagar", "nawanshahr": "shahid bhagat singh nagar",
    "nawanshahar": "shahid bhagat singh nagar", "ropar": "rupnagar",
    "bhatinda": "bathinda", "firozpur": "ferozepur", "ferozpur": "ferozepur",
    "f g sahib": "fatehgarh sahib", "fg sahib": "fatehgarh sahib",
    # NE / islands / small UTs
    # Bare "Jaintia Hills" = the UNDIVIDED pre-2012 district (RHS 2006-2012, SECC-2011)
    # -> route to the family HEAD West Jaintia Hills (HQ Jowai) so apportion() splits it
    # across West+East by 2011 population. Aliasing it to East (the small 2012 carve-out)
    # handed East the whole undivided district and left West to the imputation ladder.
    # RHS-2016's "Jaintia Hills" row genuinely means EAST (West has its own row there);
    # that one vintage is pinned in build_rhs().
    "jaintia hills": "west jaintia hills", "aizawl west": "aizawl",
    "aizwal west": "aizawl", "aizwal east": "aizawl", "aizawl east": "aizawl",
    "saiha": "siaha", "kiphere": "kiphire", "kurumkumey": "kurung kumey",
    "kurumkumay": "kurung kumey", "papumpare": "papum pare", "chunglang": "changlang",
    "bishenpur": "bishnupur", "pherjawl": "pherzawl", "pherzawal": "pherzawl",
    "lakshadweep": "lakshadweep district", "port blair": "south andamans",
    "panaji": "north goa", "shillong": "east khasi hills",
    "silvassa": "dadra and nagar haveli", "pondicherry": "puducherry",
    "d and n haveli": "dadra and nagar haveli",
    "agartala": "west tripura",
    # Chhattisgarh
    "balodabazar": "balodabazar bhatapara", "baloda bazar": "balodabazar bhatapara",
    "dantewada": "dakshin bastar dantewada", "kanker": "uttar bastar kanker",
    "kawardha": "kabeerdham", "koriya": "korea", "koria": "korea",
    "bemetera": "bemetara", "bhilai": "durg", "jagdalpur": "bastar",
    "ambikapur": "surguja",
    # final batch: 4A rural-district splits, RHS-2017 spellings, NABARD, NMC extras
    "jaipur rural": "jaipur gramin", "jaipur i": "jaipur", "jaipur ii": "jaipur",
    "jodhpur rural": "jodhpur gramin",
    "junagarh": "junagadh", "l and spiti": "lahaul and spiti",
    "uttarakannada": "uttara kannada", "kasargode": "kasaragod",
    "agar": "agar malwa", "mandsoure": "mandsaur", "jagatsinpur": "jagatsinghapur",
    "nawarapur": "nabarangpur", "jagityala": "jagitial", "manchiryal": "mancherial",
    "g b nagar": "gautam buddha nagar", "sant k nagar": "sant kabir nagar",
    "st ravidas nagar": "bhadohi", "paschim burdwan": "paschim bardhaman",
    "purba burdwan": "purba bardhaman", "gajraula": "amroha",
    "bachupally": "medchal malkajgiri",
    # teammate pulls (SECC/HMIS/Jan Aushadhi): OCR garbles + camel/spaced renames
    "korya": "korea", "kabirdham": "kabeerdham", "mungali": "mungeli",
    "byapur": "bijapur", "navsarl": "navsari", "seon": "seoni", "shivpurt": "shivpuri",
    "artyalur": "ariyalur", "auralya": "auraiya", "bynor": "bijnor", "hardol": "hardoi",
    "mahamaya nagar": "hathras", "lawngtla 1": "lawngtlai", "lawngtla": "lawngtlai",
    "lunglet": "lunglei", "punch": "poonch", "shupiyan": "shopian", "ribhoi": "ri bhoi",
    "konaseema": "dr b r ambedkar konaseema", "lahul spiti": "lahaul and spiti",
    "s a snagar": "s a s nagar",
    "medinipur east": "purba medinipur", "medinipur west": "paschim medinipur",
    "manendragarh chirimiri bharatpur": "manendragarh chirmiri bharatpur m c b",
    "manendragarh chirmiri bharatpur": "manendragarh chirmiri bharatpur m c b",
}
AMBIGUOUS = {"delhi", "navi mumbai", "imphal", "andamans", "andaman",
             "total", "grand total"}

# --- Tier-2 manual placement for NMC UG colleges whose District cell is a bare metro label
# (Delhi/Navi Mumbai/Imphal) or a truncated/garbled PDF locality that no alias/fuzzy rule can
# safely reach. Keyed on a distinctive lowercase substring of the *college name* (verified to
# match exactly one college each), so we place by the institution's known campus locality rather
# than guessing from the town fragment. Value = exact current-LGD district. Recovers the last
# 6,492 MBBS seats (50 colleges) -> 100% of NMC seats placed. Audit: nmc_manual_placements.csv.
NMC_COLLEGE_OVERRIDE = {
    # --- named town / truncated-district localities (each unambiguously in one district) ---
    "government medical college, paderu": "Alluri Sitharama Raju",
    "dr. p.s.i. medical college": "Krishna",                       # Chinoutpalli, Gannavaram
    "tomo riba institute": "Papum Pare",                           # Naharlagun
    "netaji subhas medical college & hospital, amhara": "Patna",   # Amhara, Bihta
    "radha devi jageshwari": "Muzaffarpur",                        # Turki
    "sal institute of medical sciences": "Ahmedabad",
    "dr. rajendar prasad government medical college": "Kangra",    # Tanda
    "adichunchanagiri institute of medical sciences": "Mandya",    # Bellur / B.G. Nagara
    "sri siddhartha medical coll": "Tumakuru",
    "m e s medical college": "Malappuram",                         # Perinthalmanna
    "dr. n y tasgaonkar": "Raigad",                                # Karjat (Raigad)
    "pa sangama": "Ri Bhoi",
    "zoram medical college": "Aizawl",                             # Falkawn
    "driems institute of health sciences": "Cuttack",              # Kairapari, Tangi
    "rimt medical college": "Fatehgarh Sahib",                     # Mandi Gobindgarh
    "american international institute of medical sciences": "Udaipur",   # Bedwas
    "government institute of medical sciences, kasna": "Gautam Buddha Nagar",  # Greater Noida
    "lakhimpuri kheri": "Kheri",                                   # Autonomous State Medical College
    "rajkiya medical college jalaun": "Jalaun",                    # Orai
    "tamralipto government medical college": "Purba Medinipur",    # Tamluk
    "colle ge, bolpur": "Birbhum",                                 # Santiniketan Medical College, Bolpur
    "diamond harbour government medical college": "South 24 Parganas",
    "employees state insurance corporation medical college, joka": "South 24 Parganas",
    # --- Telangana peri-Hyderabad / named towns ---
    "dr. vrk womens medical college": "Ranga Reddy",               # Aziznagar
    "government medical college, bhadradri": "Bhadradri Kothagudem",
    "all india institute of medical sciences, bibinagar": "Yadadri Bhuvanagiri",
    "mediciti institute of medical sciences": "Medchal Malkajgiri",  # Ghanpur
    "government medical college, maheshwaram": "Ranga Reddy",
    "government medical college, narsampet": "Warangal",
    "shadan institute of medical sciences": "Ranga Reddy",         # Peerancheru
    "government medical college, quthbullapur": "Medchal Malkajgiri",
    "surabhi institute of medical sciences": "Siddipet",
    "bhaskar medical college": "Ranga Reddy",                      # Yenkapally
    # --- metro labels placed by the institution's known campus locality ---
    "vardhman mahavir medical college": "South",                   # Safdarjung / Ansari Nagar
    "army college of medical sciences": "New Delhi",               # Delhi Cantonment
    "all india institute of medical sciences, new delhi": "South", # Ansari Nagar
    "university college of medical sciences": "Shahdara",          # GTB, Dilshad Garden
    "lady hardinge": "New Delhi",
    "maulana azad medical college": "Central",
    "atal bihari vajpayee institute of medical sciences and dr. rml": "New Delhi",
    "dr. baba saheb ambedkar medical college": "North West",       # Rohini
    "north delhi muncipal corporation medical college": "North",   # Hindu Rao
    "hamdard institute of medical sciences": "South East",         # Hamdard Nagar / Tughlakabad
    "terna medical college": "Thane",                              # Nerul
    "padmashree dr. d.y.patil medical college, navi mumbai": "Thane",  # Nerul
    "mahatma gandhi missions medical college, navi mumbai": "Raigad",  # Kamothe
    "mahatma gandhi amissions medical college": "Raigad",          # Kamothe (typo variant)
    "regional institute of medical sciences, imphal": "Imphal West",
    "shija academy": "Imphal West",
    "jawaharlal nehru institute of medical sciences, porompet": "Imphal East",
}

# --- NMC PG seat matrix: state-label repairs (PDF space-splits / spellings norm_state misses) ---
NMC_PG_STATE_FIX = {
    "maharashtr a": "Maharashtra", "chhattisgar h": "Chhattisgarh",
    "uttarakhan d": "Uttarakhand", "uttrakhand": "Uttarakhand",
    "rajisthan": "Rajasthan", "tamilnadu": "Tamil Nadu",
    "dadara nagar haveli": "Dadra and Nagar Haveli",
}

# --- Tier-2 manual placement for the 34 PG-only institutes (no MBBS => absent from the UG file:
# standalone cancer / cardiac / mental-health / kidney / defence-services hospitals + a few
# garbled rows) whose comma-town fragment the resolver can't reach. Keyed on a distinctive
# lowercase College substring (each hits exactly one PG college); value = current-LGD district.
# Recovers the last 1,429 PG seats -> 100% placed. Audit: nmc_pg_manual_placements.csv.
NMC_PG_COLLEGE_OVERRIDE = {
    "d y patil medical college, nerul": "Thane",                    # Nerul, Navi Mumbai
    "post graduate institute of medical science navi mumbai": "Thane",
    "research centre chengalpattu": "Chengalpattu",                 # SRM, Kattankulathur
    "goa medical college": "North Goa",                             # Bambolim
    "government medical college, ongole": "Prakasam",
    "agartala government college": "West Tripura",
    "e.s.i.c. medical college & hospital k.k. nagar chennai": "Chennai",
    "maharashtra post graudate institute": "Nashik",               # MPGIMER (source typo 'graudate')
    "government institute of medical sciences greater noida": "Gautam Buddha Nagar",
    "pimpri chinchwad municipal corporation": "Pune",              # Pimpri
    "sri jayadeva institute of cardiology": "Bengaluru Urban",
    "prakash institute of medical sciences": "Sangli",             # Islampur / Sangli
    "institute of navel medicine": "Mumbai",                       # INHS Asvini, Colaba (typo 'navel')
    "hindu rao hospital": "North",                                 # Malka Ganj, Delhi
    "namo medical education and research institute": "Dadra and Nagar Haveli",  # Silvassa
    "borooah cancer institute": "Kamrup Metropolitan",             # Guwahati
    "institute of human behaviour and allied sciences": "Shahdara",  # IHBAS, Dilshad Garden
    "moopen s medical college": "Wayanad",
    "homi bhabha cancer hospital, varanasi": "Varanasi",
    "institute of kidney diseases and research centre": "Ahmedabad",  # IKDRC
    "shija academay": "Imphal West",                               # source typo 'academay'
    "bhagwan mahavir institute of medical sciences, pawapuri": "Nalanda",
    "yakkara": "Palakkad",                                         # GMC Yakkara, Palakkad
    "vallabhai patel chest institute": "North",                    # VPCI, DU North Campus
    "chittaranjan national cancer institute": "Kolkata",
    "institute of aerospace medicine": "Bengaluru Urban",
    "chandimandir": "Panchkula",                                   # Command Hospital, Chandimandir
    "perundurai": "Erode",                                         # GMC Perundurai
    "chacha nehru bal chikitsalaya": "East",                       # Geeta Colony, Delhi
    "gwalior mansik arogyashala": "Gwalior",
    "sonelal patel": "Pratapgarh",                                 # ASMC Pratapgarh, UP
    "homi bhabha cancer hospital, sangrur": "Sangrur",
    "malabar cancer centre": "Kannur",                             # Thalassery
    "irungalur": "Tiruchirappalli",                                # Chennai MC, Irungalur, Trichy
}

# ======================================================================================
# 2 | LGD index + resolver (candidate variants -> alias -> exact -> fuzzy)
# ======================================================================================
lgd = pd.read_csv(os.path.join(RAW, "district_master_lgd", "lgd_districts.csv"), dtype=str)
lgd["dk"] = lgd["district_name_english"].map(norm)
lgd["sk"] = lgd["state_name_english"].map(norm_state)
BY_STATE = {sk: dict(zip(g["dk"], g["district_code"])) for sk, g in lgd.groupby("sk")}
NAME_CODES = lgd.groupby("dk")["district_code"].apply(list).to_dict()
ALL_NAMES = list(NAME_CODES.keys())
CODE_NAME = dict(zip(lgd["district_code"], lgd["district_name_english"]))
CODE_STATE = dict(zip(lgd["district_code"], lgd["state_name_english"]))

def _candidates(name):
    """Yield normalised name variants: full, before/inside parens, before/after slash,
    each also with a trailing 'district' token stripped."""
    raw = str(name)
    parts = [raw]
    dc = _decamel(raw)                                   # SouthTwentyFourParganas -> spaced
    if dc != raw:
        parts.append(dc)
    stripped = re.sub(r"_[A-Za-z]{2,4}$", "", raw)       # drop trailing _Cht/_Hp/_Up state tag
    if stripped != raw:
        parts.append(stripped)
        if _decamel(stripped) != stripped:
            parts.append(_decamel(stripped))
    if "(" in raw:
        parts.append(raw.split("(")[0])
        m = re.search(r"\(([^)]*)\)", raw)
        if m:
            parts.append(m.group(1))
    if "/" in raw:
        parts += [raw.split("/")[0], raw.split("/")[-1]]
    out, seen = [], set()
    for p in parts:
        b = norm(p)
        nd = re.sub(r"\s+", " ", re.sub(r"\bdistrict\b", "", b)).strip()
        # despaced variant repairs PDF extraction artifacts ('Kancheepur am' -> 'kancheepuram')
        for v in (b, nd, b.replace(" ", ""), nd.replace(" ", "")):
            if v and v not in seen:
                seen.add(v); out.append(v)
    return out

def resolve(state_raw, dist_raw, fuzz_state=90, fuzz_india=94):
    sk = norm_state(state_raw)
    cands = _candidates(dist_raw)
    if not cands:
        return None, "empty", 0
    if re.search(r"\btotal\b|all india|grand|\bzone\b|districts *=", cands[0]):
        return None, "junk", 0
    saw_ambiguous = False
    # exact / alias passes
    for dk0 in cands:
        if dk0 in AMBIGUOUS:
            saw_ambiguous = True; continue
        dk = STATE_SCOPED.get((sk, dk0)) or GLOBAL.get(dk0) or dk0
        aliased = dk != dk0
        if sk in BY_STATE and dk in BY_STATE[sk]:
            return BY_STATE[sk][dk], ("alias_exact" if aliased else "exact_state"), 100
        if dk in NAME_CODES and len(NAME_CODES[dk]) == 1:
            return NAME_CODES[dk][0], ("alias_name_india" if aliased else "exact_name_india"), 97
    if saw_ambiguous:
        return None, "ambiguous", 0
    # fuzzy passes
    best = 0
    for dk0 in cands:
        dk = STATE_SCOPED.get((sk, dk0)) or GLOBAL.get(dk0) or dk0
        if sk in BY_STATE and BY_STATE[sk]:
            m = process.extractOne(dk, list(BY_STATE[sk].keys()), scorer=fuzz.token_sort_ratio)
            if m and m[1] >= fuzz_state:
                return BY_STATE[sk][m[0]], "fuzzy_state", int(m[1])
            if m: best = max(best, m[1])
        m = process.extractOne(dk, ALL_NAMES, scorer=fuzz.token_sort_ratio)
        if m and m[1] >= fuzz_india and len(NAME_CODES[m[0]]) == 1:
            return NAME_CODES[m[0]][0], "fuzzy_india", int(m[1])
        if m: best = max(best, m[1])
    return None, "unmatched", int(best)

# ======================================================================================
# 3 | Current-frame spine + 2011 population weights + parent families (from views)
# ======================================================================================
def _sid(x):
    x = str(x).strip()
    return x[:-2] if x.endswith(".0") else x

v2 = pd.read_csv(os.path.join(FRAME, "view2_districts_all_current.csv"), dtype=str)
SPINE = v2[["district_code", "district_name_english", "state_name_english",
            "is_post2011_district", "pc11_district_id",
            "parent_pc11_district_code", "parent_district_name"]].copy()
SPINE["district_code"] = SPINE["district_code"].map(_sid)
SPINE["is_post2011"] = SPINE["is_post2011_district"].str.lower().eq("true")
SPINE["sk"] = SPINE["state_name_english"].map(norm_state)
SPINE["pc11_district_id"] = SPINE["pc11_district_id"].map(lambda x: _sid(x) if pd.notna(x) else x)
SPINE["parent_pc11_district_code"] = SPINE["parent_pc11_district_code"].map(
    lambda x: _sid(x) if pd.notna(x) else x)

# family key = the 2011 parent census district code
def _family(row):
    if row["is_post2011"]:
        p = row["parent_pc11_district_code"]
        return p if (pd.notna(p) and p not in ("", "nan")) else "solo_" + row["district_code"]
    return row["pc11_district_id"]
SPINE["family"] = SPINE.apply(_family, axis=1)

# 2011 population per current district = sum of its sub-districts' 2011 PCA population (view3)
v3 = pd.read_csv(os.path.join(FRAME, "view3_subdistricts_all_current.csv"),
                 dtype={"district_code": str})
v3["district_code"] = v3["district_code"].map(_sid)
POP = (v3.groupby("district_code")["pc11_pca_tot_p"]
         .apply(lambda s: pd.to_numeric(s, errors="coerce").sum())
         .to_dict())
# fallback to view2 district pop for heads whose sub-districts summed to 0
_v2pop = dict(zip(SPINE["district_code"], pd.to_numeric(v2["pc11_pca_tot_p"], errors="coerce")))
for c in SPINE["district_code"]:
    if not POP.get(c) or POP.get(c) == 0:
        fp = _v2pop.get(c)
        POP[c] = fp if (fp and fp > 0) else 0.0

# ---- Curated 2011-population fix: five post-2011 districts the SHRUG sub-district join
# cannot reach. Four Tripura children (Khowai/Sepahijala/Gomati/Unakoti) and Assam's Bajali
# carry LGD sub-district census codes of 00000, so their 2011 PCA population never joins AND
# the parent-map inference fails -> the child lands at pop2011=0 (blank per-capita rates)
# while its retained 2011 parent keeps the *whole undivided* total. We reallocate each
# affected 2011 parent's population across parent + carved children:
#   * Tripura -- split the parent's current frame total by published Census-2011 district
#     populations used as SHARES, rescaled so each family sums EXACTLY to that frame total
#     (state total conserved to the person). West family already sums to 1,725,739; South and
#     North rescale by ~0.979 / ~0.971 to absorb the DCHB-vs-SHRUG vintage gap.
#   * Assam -- current Barpeta (frame) already excludes the Bajali "(Pt)" sub-districts, so
#     Bajali is the missing residual of old undivided Barpeta (SHRUG pc11 dist 303 = 1,693,622).
# Published district shares: Census-2011 DCHB, en.wikipedia.org/wiki/List_of_districts_of_Tripura.
# Keyed by current LGD district_code; documented in data/processed/crosswalk/README.md.
POP_FAMILY_FIX = {                                          # parent_code: {code: published 2011 pop}
    "272": {"272": 918200, "652": 327564, "653": 479975},   # West Tripura | Khowai | Sepahijala
    "271": {"271": 453079, "654": 441538},                  # South Tripura | Gomati
    "270": {"270": 415946, "655": 298574},                  # North Tripura | Unakoti
}
for parent, shares in POP_FAMILY_FIX.items():
    total = float(POP.get(parent, 0.0))                     # frame total currently on the parent
    denom = float(sum(shares.values()))
    alloc = {c: round(total * v / denom) for c, v in shares.items()}
    alloc[parent] += int(round(total)) - sum(alloc.values())  # rounding residual -> parent (exact conservation)
    POP.update({c: float(v) for c, v in alloc.items()})
BARPETA_OLD_2011 = 1693622.0                                # SHRUG pc11 dist 303 (undivided Barpeta)
POP["739"] = BARPETA_OLD_2011 - float(POP["280"])           # Bajali <- old Barpeta minus current Barpeta

# ---- (d) urban-core / unmatched-tehsil repair for districts that did NOT lose territory. SHRUG
# drops each district's `99999` urban bucket (no LGD sub-district to join) and a few unmatched
# named tehsils, so the view3 tehsil sum undercounts (Bengaluru 8.44M, Chennai 1.94M, Mumbai, the
# whole Kolkata metro, Thiruvallur, Tiruppur...). The district-level 2011 PCA (view2
# `pc11_pca_tot_p` == the SHRUG district total) is the complete, authoritative figure and is safe
# for any established district that is NOT a 2011 parent of a post-2011 child -- it kept its whole
# 2011 territory, so distPCA is its current total and cannot double-count. Parents are detected
# from view3 itself: the 2011 census districts (`pc11_district_id`) that fed tehsils to any
# post-2011 district. Districts that DID lose area -- including via a MULTI-parent child assigned
# to another family (Sikar -> Neem Ka Thana, in Jhunjhunu's family) -- are skipped and left as a
# documented undercount (crosswalk/README.md §3(d)). Tripura/Barpeta parents are added explicitly:
# their children's 00000 tehsils never join view3, so they don't show up as contributors. Lifts
# ~54 districts; national pop2011 stays <= the Census-2011 total (no double-count), verified.
_postdc = set(SPINE.loc[SPINE["is_post2011"], "district_code"])
_contrib = set(pd.to_numeric(v3.loc[v3["district_code"].isin(_postdc), "pc11_district_id"],
                             errors="coerce").dropna().astype(int))          # 2011 parents of any child
_lost_parent = set(POP_FAMILY_FIX) | {"280"}      # Tripura + Barpeta: 00000-tehsil kids hide their parent
_cens = dict(zip(SPINE["district_code"], SPINE["pc11_district_id"]))         # current dc -> 2011 census code
for c, is_post in zip(SPINE["district_code"], SPINE["is_post2011"]):
    if is_post or c in _lost_parent:
        continue
    k = _cens.get(c)
    if k in (None, "", "nan") or pd.isna(k) or int(float(k)) in _contrib:
        continue
    dp = _v2pop.get(c)
    if dp and dp > (POP.get(c) or 0.0):
        POP[c] = float(dp)

# ---- (e) Split-family 99999 repair (West Bengal). Four 2011 parents split into a rural retained
# parent + a district holding the urban core (Asansol-Durgapur, Siliguri, ...); the whole family
# shortfall is that ONE 99999 lump, which belongs to a SPECIFIC member, so proportional-by-pop
# would wrongly hand the city to the rural sibling. Restore each family to the retained parent's
# SHRUG total (its distPCA) split by PUBLISHED Census-2011 district populations (DCHB via
# en.wikipedia.org/wiki/List_of_districts_of_West_Bengal); the four reconcile to the SHRUG parent
# total (three to the person, Jhargram/Paschim-Medinipur within 948). The OTHER 23 split families'
# shortfall is distributed unmatched tehsils entangled with multi-parent carving (the newest
# AP/Rajasthan children draw from 2+ old districts) with no clean 2011 retabulation to key on --
# left as a documented residual; per-capita there is indicative (crosswalk/README.md §3(d)).
SPLIT_FAMILY_SHARES = {                       # retained-parent dc : {member dc: published Census-2011 pop}
    "306": {"306": 4835532, "704": 2882031},  # Purba Bardhaman | Paschim Bardhaman  (parent pc11 335)
    "309": {"309": 1595181, "702":  251642},  # Darjeeling | Kalimpong               (parent pc11 327)
    "318": {"318": 4776909, "703": 1136548},  # Paschim Medinipur | Jhargram         (parent pc11 344)
    "314": {"314": 2381596, "664": 1491250},  # Jalpaiguri | Alipurduar              (parent pc11 328)
    # -- non-WB families added in the 4th correctness pass: the view3 tehsil join loses a
    # member-specific chunk (urban bucket / unmatched tehsils), so the family's POP RATIO --
    # the apportionment weights AND the GHS-POP PAIR_FIX shares -- was skewed, not just the
    # level. Only families whose published member populations reconcile against the SHRUG
    # parent total are added (Tirap and Kancheepuram to the person, Jaintia -0.6% DCHB
    # vintage gap, same as Tripura); the rest stay documented in crosswalk/README.md.
    "275": {"275":  270352, "657":  122436},  # West Jaintia Hills | East Jaintia Hills (was 53/47, true 69/31)
    "574": {"574": 1442008, "730": 2556244},  # Kancheepuram | Chengalpattu             (was 41/59, true 36/64)
    "239": {"239":   51381, "666":   60594},  # Tirap | Longding                        (was 70/30, true 46/54)
    # -- v1.4.9: the two families flagged "provably broken" in the 4th pass, closed with
    # citable member figures. Aizawl|Saitual: the tehsil join lost Aizawl's urban core
    # (family summed 69,507 vs census 400,309), handing Saitual 46% of the apportionment
    # weight on ~8% of the people (its IPD 569/1000 / lab-tests top-3 artifacts). Saitual
    # member figure = ORGI/Delimitation-derived 2011 pop in current boundaries (Ngopa
    # 18,730 + Phullen 13,303; citypopulation.de/en/india/admin/mizoram/794__saitual).
    # Documented approximation (README 3(a4)(iii)): includes Champhai-origin Ngopa
    # (+18.7k) but excludes the Saitual-town slice of 2011-Thingsulthliah (-11.6k+),
    # partially offsetting; the frame is single-parent (Saitual->Aizawl) so a finer
    # two-parent split has nowhere to live. Baksa|Tamulpur: Wikipedia Baksa demographics
    # publish residual Baksa 560,925 after the Tamulpur carve => Tamulpur 389,150
    # (Tamulpur+Goreswar ACs); reconciles to SHRUG's undivided 950,075 to the person.
    "261": {"261":  368276, "727":   32033},  # Aizawl | Saitual                        (was 54/46, true 92/8)
    "616": {"616":  560925, "756":  389150},  # Baksa | Tamulpur                        (was 54/46, true 59/41)
}
for parent, shares in SPLIT_FAMILY_SHARES.items():
    total = float(_v2pop.get(parent, 0.0))    # SHRUG parent total = retained parent's undivided distPCA
    denom = float(sum(shares.values()))
    alloc = {c: round(total * v / denom) for c, v in shares.items()}
    alloc[parent] += int(round(total)) - sum(alloc.values())   # rounding residual -> retained parent
    POP.update({c: float(v) for c, v in alloc.items()})

SPINE["pop2011"] = SPINE["district_code"].map(POP).fillna(0.0)

FAM_MEMBERS = SPINE.groupby("family")["district_code"].apply(list).to_dict()
FAM_HEAD = {f: (g[~g["is_post2011"]]["district_code"].iloc[0]
                if (~g["is_post2011"]).any() else None)
            for f, g in SPINE.groupby("family")}
D2FAM = dict(zip(SPINE["district_code"], SPINE["family"]))
POST2011 = dict(zip(SPINE["district_code"], SPINE["is_post2011"]))

# ======================================================================================
# 4 | Shared helpers: match a source frame, and forward-apportion undivided lumps
# ======================================================================================
UNMATCHED = []   # accumulates report rows across sources
NAMEMAP  = []    # accumulates resolved gazetteer rows

def match_frame(df, state_col, dist_col, source, value_cols):
    """Resolve (state,name)->district_code for every row; log unmatched/ambiguous.
    Returns df with columns [district_code] + value_cols, dropping unresolved rows."""
    recs = df[[state_col, dist_col]].apply(
        lambda r: resolve(r[state_col], r[dist_col]), axis=1, result_type="expand")
    df = df.copy()
    df["district_code"], df["_method"], df["_score"] = recs[0], recs[1], recs[2]
    ok = df[df["district_code"].notna()].copy()
    for _, r in ok.iterrows():
        NAMEMAP.append({"source": source, "src_state": r[state_col], "src_district": r[dist_col],
                        "district_code": _sid(r["district_code"]),
                        "lgd_district": CODE_NAME.get(_sid(r["district_code"])),
                        "lgd_state": CODE_STATE.get(_sid(r["district_code"])),
                        "method": r["_method"], "score": r["_score"]})
    for _, r in df[df["district_code"].isna() & ~df["_method"].isin(["junk"])].iterrows():
        rec = {"source": source, "src_state": r[state_col], "src_district": r[dist_col],
               "reason": r["_method"], "best_fuzzy_score": r["_score"]}
        for vc in value_cols:
            rec[vc] = r.get(vc)
        UNMATCHED.append(rec)
    ok["district_code"] = ok["district_code"].map(_sid)
    return ok[["district_code"] + value_cols]

def to_spine(matched, value_cols, agg="sum"):
    """Aggregate matched rows to one row per district_code, reindex onto the 785 spine."""
    for vc in value_cols:
        matched[vc] = pd.to_numeric(matched[vc], errors="coerce")
    gb = matched.groupby("district_code")[value_cols]
    # min_count=1: a district whose source rows are all blank stays NaN ("reported nothing"),
    # not 0 -- absent_zero is the only opt-in to zero. Plain sum() fabricates 0 there.
    g = gb.sum(min_count=1) if agg == "sum" else gb.agg(agg)
    out = SPINE[["district_code"]].merge(g, on="district_code", how="left")
    return out

def apportion(out, value_cols):
    """Forward-crosswalk: where an old undivided district (family head) is present but its
    post-2011 children are absent, redistribute the head's extensive totals across all
    family members by 2011 population. Adds is_apportioned flag. Ratios are NOT apportioned."""
    val = out.set_index("district_code")[value_cols]
    has_val = val.notna().any(axis=1)
    flag = pd.Series(False, index=val.index)
    for fam, members in FAM_MEMBERS.items():
        if len(members) < 2:
            continue
        head = FAM_HEAD.get(fam)
        if head is None or head not in val.index or not has_val.get(head, False):
            continue
        present = [m for m in members if has_val.get(m, False)]
        if set(present) != {head}:      # source already split (children present) -> leave
            continue
        weights = np.array([POP.get(m, 0.0) for m in members], dtype=float)
        if weights.sum() <= 0:
            weights = np.ones(len(members))
        weights = weights / weights.sum()
        totals = val.loc[head, value_cols].astype(float)
        for m, w in zip(members, weights):
            val.loc[m, value_cols] = (totals * w).values
            flag.loc[m] = True
    out = out.set_index("district_code")
    out[value_cols] = val[value_cols]
    out["is_apportioned"] = flag
    return out.reset_index()

def redistribute_lumped_state(out, sk, value_cols):
    """Source reports a whole state/UT as ONE unit (RBI Statement 4A reports all of Delhi
    under a single 'New Delhi' row). Spread that lump across every LGD district of the
    state/UT by 2011 population, so no district is left blank and none carries the whole-NCT
    total. Extensive columns only; ratios/per-capita are recomputed downstream."""
    codes = SPINE[SPINE["sk"] == sk]["district_code"].tolist()
    if not codes:
        return out
    vals = out[out["district_code"].isin(codes)][value_cols].apply(
        pd.to_numeric, errors="coerce")
    if not vals.notna().any().any():   # state absent from source: leave NaN, don't spread 0
        return out
    totals = vals.sum()
    w = np.array([POP.get(c, 0.0) for c in codes], dtype=float)
    w = np.ones(len(codes)) if w.sum() <= 0 else w / w.sum()
    out = out.set_index("district_code")
    if "is_apportioned" not in out.columns:
        out["is_apportioned"] = False
    for c, wi in zip(codes, w):
        out.loc[c, value_cols] = (totals * wi).values
        out.loc[c, "is_apportioned"] = True
    return out.reset_index()

def crosswalk_file(data, state_col, dist_col, value_cols, source="custom",
                   extensive=True, apportion_undivided=True, lumped_states=None,
                   absent_zero=False, add_names=True, out_csv=None, agg="sum"):
    """Generic ONE-CALL crosswalk of any district-keyed file onto the 785-LGD frame.

    This is the reusable entry point for teammates: it chains the same primitives the
    per-source adapters below use (match_frame -> to_spine -> apportion/redistribute), so any
    file with a free-text (state, district) column pair lands on the current 785-district
    frame with all ~330 aliases + the fuzzy matcher applied. It does NOT do general cleaning
    (types, dedup, imputation, units, outliers) -- only district-name -> LGD harmonisation.

        import build_crosswalk as bx
        df = bx.crosswalk_file("mydata.csv", "State", "District",
                               ["cases", "hospitals"], source="MyDataset")

    Parameters
      data          DataFrame, or path to a .csv/.xlsx holding (state, district) + value cols.
      state_col     column name with the free-text state.
      dist_col      column name with the free-text district.
      value_cols    list of value columns to carry through (coerced numeric).
      source        label recorded in name_map / unmatched_report (for provenance).
      extensive     True for counts/amounts (summable & apportionable); False for
                    ratios/per-capita -- those are NEVER split (recompute them from parts).
      apportion_undivided  forward-split an old undivided parent across its post-2011 children
                    by 2011 population. Only applied when extensive=True.
      lumped_states list of state names reported as ONE lump (e.g. ["Delhi"] like RBI 4A),
                    spread across that state's districts by 2011 population. extensive only.
      absent_zero   fill unmatched districts with 0 (use when 'absent = genuinely none',
                    e.g. facility counts) instead of leaving NaN ('absent = not reported').
      add_names     prepend district_name_english / state_name_english from the spine.
      out_csv       if given, also write the 785-row result there.
      agg           aggregation when multiple source rows hit one district (default 'sum').

    Returns a 785-row DataFrame keyed by district_code. Unresolved rows are appended to the
    module-level UNMATCHED report and resolved rows to NAMEMAP (write them yourself if wanted).
    """
    if isinstance(data, str):
        data = (pd.read_excel(data) if data.lower().endswith((".xlsx", ".xls"))
                else pd.read_csv(data, dtype=str))
    matched = match_frame(data, state_col, dist_col, source, value_cols)
    out = to_spine(matched, value_cols, agg=agg)
    if extensive and apportion_undivided:
        out = apportion(out, value_cols)
    if extensive and lumped_states:
        for st in lumped_states:
            out = redistribute_lumped_state(out, norm_state(st), value_cols)
    if absent_zero:
        out[value_cols] = out[value_cols].fillna(0)
    if add_names:
        out = SPINE[["district_code", "district_name_english", "state_name_english"]].merge(
            out, on="district_code", how="right")
    if out_csv:
        out.to_csv(out_csv, index=False)
    return out

# ======================================================================================
# 5 | Source: RHS district health infrastructure panel (2006-2019)
# ======================================================================================
def _find(df, *subs):
    for c in df.columns:
        cl = str(c).lower()
        if all(s in cl for s in subs):
            return c
    return None

RHS_YEARS = [
    dict(year=2006, file="District-wise_Health_Centres_RHS_2006.csv",
         state=("states_union", "total"), dist=("name", "district")),
    dict(year=2010, file="District-wise_Health_Centres_March_2010.csv",
         state=("states_union",), dist=("name", "district")),
    dict(year=2012, file="District-wise_Health_Centres_March_2012.csv",
         state=("states_union",), dist=("name", "district")),
    dict(year=2016, file="District-wise_Health_Centres_March_2016_1.csv",
         state=("states/union",), dist=("name", "district")),
    dict(year=2017, file="District-wise_Health_Centres_March_2017_1.csv",
         state=("states/union",), dist=("district",)),
    dict(year=2019, file="District-wise_Health_Centres_March_2019_RuralUrban.csv",
         state=("states_union",), dist=("name", "district")),
]
FAC = ["sub_centres", "phcs", "chcs", "sub_divisional_hospitals",
       "district_hospitals", "hwc_sc", "hwc_phc"]

def build_rhs():
    panels = []
    for y in RHS_YEARS:
        df = pd.read_csv(os.path.join(RAW, "data_gov_in", y["file"]), dtype=str)
        sc_ = _find(df, *y["state"]); dc_ = _find(df, *y["dist"])
        # facility columns (first match wins -> plain PHC/SC before HWC variants in 2019)
        cols = {
            "sub_centres": _find(df, "sub", "centre") or _find(df, "scs"),
            "phcs": _find(df, "phc"),
            "chcs": _find(df, "chc"),
            "sub_divisional_hospitals": _find(df, "sub", "divisional"),
            "district_hospitals": _find(df, "district", "hospital"),
            "hwc_sc": _find(df, "hwc", "sc"),
            "hwc_phc": _find(df, "hwc", "phc"),
        }
        # in 2019 'phcs'/'scs' first-match already excludes hwc (hwc cols come later); ok
        keep = {v: k for k, v in cols.items() if v is not None}
        sub = df[[sc_, dc_] + list(keep)].rename(columns=keep)
        val_cols = [c for c in FAC if c in sub.columns]
        # Single-district UTs (Chandigarh / D&N Haveli / Lakshadweep) label their one real
        # district row "Total" (2016/17) or "Total - District UT" (2019); the junk filter
        # would silently drop them -- rewrite the district cell to the UT's own name.
        ut1 = sub[sc_].astype(str).map(norm_state).isin(
            {"chandigarh", "lakshadweep", "the dadra and nagar haveli and daman and diu"})
        is_tot = sub[dc_].astype(str).str.strip().str.lower().str.startswith("total")
        not_dd = ~sub[sc_].astype(str).str.strip().str.lower().str.startswith("daman")
        sub.loc[ut1 & is_tot & not_dd, dc_] = sub.loc[ut1 & is_tot & not_dd, sc_]
        # RHS-2016 is on the post-2012 Meghalaya frame but labels EAST Jaintia Hills with
        # the bare undivided name (West Jaintia Hills has its own row in the same file);
        # pin it to East so the undivided-name head-alias (2006-2012 vintages) doesn't
        # collide the two rows onto West.
        if y["year"] == 2016:
            is_meg = sub[sc_].astype(str).map(norm_state).eq("meghalaya")
            is_jh = sub[dc_].astype(str).str.strip().str.lower().eq("jaintia hills")
            sub.loc[is_meg & is_jh, dc_] = "East Jaintia Hills"
        matched = match_frame(sub, sc_, dc_, f"RHS_{y['year']}", val_cols)
        out = to_spine(matched, val_cols)
        out = apportion(out, val_cols)
        if y["year"] == 2019:
            # 2019 source anomaly (verified, the only partially-blank row in the panel):
            # Kalimpong's row leaves Sub-Centres blank while Darjeeling's 230 equals the
            # undivided 2012 total to the digit (198.66 + 31.34) -- the SC column is still
            # lumped under Darjeeling. Carve it back out by the same 2011-population shares.
            nc = dict(zip(SPINE["district_name_english"], SPINE["district_code"]))
            d_c, k_c = nc["Darjeeling"], nc["Kalimpong"]
            oi = out.set_index("district_code")
            if pd.isna(oi.loc[k_c, "sub_centres"]) and pd.notna(oi.loc[d_c, "sub_centres"]):
                w = POP.get(k_c, 0.0) / (POP.get(k_c, 0.0) + POP.get(d_c, 0.0))
                lump = float(oi.loc[d_c, "sub_centres"])
                oi.loc[k_c, "sub_centres"] = lump * w
                oi.loc[d_c, "sub_centres"] = lump * (1 - w)
                oi.loc[[k_c, d_c], "is_apportioned"] = True
            out = oi.reset_index()
        out["year"] = y["year"]
        panels.append(out)
    panel = pd.concat(panels, ignore_index=True)
    # tidy column order
    front = ["district_code", "year", "is_apportioned"]
    panel = panel[front + [c for c in FAC if c in panel.columns]]
    panel = SPINE[["district_code", "district_name_english", "state_name_english"]].merge(
        panel, on="district_code", how="right")
    panel.to_csv(os.path.join(CW, "rhs_health_panel.csv"), index=False)

    # latest snapshot (2019) with HWC folded in for comparability (CLAUDE.md modelling note).
    # min_count=1: a district absent from the 2019 source stays NaN (-> imputation ladder);
    # fillna(0)+fillna(0) fabricated "0 sub-centres" for the ~10 all-NaN districts.
    y19 = panel[panel["year"] == 2019].copy()
    y19["sub_centres_incl_hwc"] = y19[["sub_centres", "hwc_sc"]].sum(axis=1, min_count=1)
    y19["phcs_incl_hwc"] = y19[["phcs", "hwc_phc"]].sum(axis=1, min_count=1)
    y19.to_csv(os.path.join(CW, "rhs_health_2019.csv"), index=False)
    return panel, y19

# ======================================================================================
# 6 | Source: NMC UG (MBBS) seats -- college town -> district (no apportionment; absent = 0)
# ======================================================================================
def build_nmc():
    df = pd.read_csv(os.path.join(RAW, "medical_education_tertiary",
                                  "NMC_UG_Seat_Matrix_2024-25.csv"), dtype=str)
    df["seats"] = pd.to_numeric(df["AnnualIntake"], errors="coerce")
    df["colleges"] = 1
    # Tier-2 manual placement: rewrite the District cell of the 50 colleges NMC_COLLEGE_OVERRIDE
    # covers (bare metro labels + garbled localities) to their true LGD district, so the normal
    # resolver places them exactly. Log each as an audit row.
    audit = []
    cl = df["College"].str.lower()
    for key, dist in NMC_COLLEGE_OVERRIDE.items():
        hit = cl.str.contains(key, regex=False, na=False)
        for i in df.index[hit]:
            audit.append({"college": df.at[i, "College"], "state": df.at[i, "State"],
                          "district_label_in_source": df.at[i, "District"],
                          "assigned_lgd_district": dist, "mbbs_seats": df.at[i, "AnnualIntake"]})
            df.at[i, "District"] = dist
    if audit:
        pd.DataFrame(audit).sort_values(["state", "assigned_lgd_district"]).to_csv(
            os.path.join(CW, "nmc_manual_placements.csv"), index=False)
    matched = match_frame(df, "State", "District", "NMC_UG", ["seats", "colleges"])
    out = to_spine(matched, ["seats", "colleges"])
    out = out.rename(columns={"seats": "mbbs_seats", "colleges": "mbbs_college_count"})
    # absent = genuinely no medical college -> 0 (do NOT apportion place-specific colleges)
    out[["mbbs_seats", "mbbs_college_count"]] = out[["mbbs_seats", "mbbs_college_count"]].fillna(0)
    out = SPINE[["district_code", "district_name_english", "state_name_english"]].merge(
        out, on="district_code", how="left")
    out.to_csv(os.path.join(CW, "nmc_ug_by_district.csv"), index=False)
    return out

# ======================================================================================
# 6b | Source: NMC PG seats -- the PG matrix has no District column, so each PG college is placed
#      by (1) tier-2 override for PG-only institutes, (2) exact then (3) fuzzy college-name join to
#      the UG file (which already carries a resolved district), (4) comma-town fallback through the
#      standard resolver. Place-specific like UG -> no apportionment; absent district = 0.
# ======================================================================================
def _ug_college_dc():
    """UG college-name (normalised) -> current district_code, via the same override + resolver as
    build_nmc. Returns (global_map, per_state_map) for the exact and state-scoped fuzzy PG joins."""
    ug = pd.read_csv(os.path.join(RAW, "medical_education_tertiary",
                                  "NMC_UG_Seat_Matrix_2024-25.csv"), dtype=str)
    cl = ug["College"].str.lower()
    for key, dist in NMC_COLLEGE_OVERRIDE.items():
        ug.loc[cl.str.contains(key, regex=False, na=False), "District"] = dist
    gmap, smap = {}, {}
    for _, r in ug.iterrows():
        dc, _, _ = resolve(r["State"], r["District"])
        if dc is None:
            continue
        ck = norm(r["College"])
        gmap.setdefault(ck, _sid(dc))
        smap.setdefault(norm_state(r["State"]), {}).setdefault(ck, _sid(dc))
    return gmap, smap

def build_nmc_pg():
    df = pd.read_csv(os.path.join(RAW, "medical_education_tertiary",
                                  "NMC_PG_Seat_Matrix_2024-25.csv"), dtype=str)
    df["seats"] = pd.to_numeric(df["Seats"], errors="coerce").fillna(0.0)
    df["state2"] = df["State"].map(lambda s: NMC_PG_STATE_FIX.get(norm(s), s))
    # super-specialty = degree-prefixed DM / MCh (tertiary depth / referral-pull signal); the rest
    # (MD/MS/Diploma/bare-subject rows) are broad specialty. Conservative: undegreed subject rows
    # stay in 'broad'. Course text is free-form, so match the degree prefix tolerantly.
    sup = df["Course"].str.match(r"\s*(d\s*\.?\s*m\b|m\s*\.?\s*ch)", case=False, na=False)
    df["sup_seats"] = df["seats"].where(sup, 0.0)
    agg = df.groupby(["state2", "College"], as_index=False).agg(
        seats=("seats", "sum"), sup_seats=("sup_seats", "sum"))
    agg["colleges"] = 1

    gmap, smap = _ug_college_dc()
    audit = []
    def place(row):
        col = row["College"]; cl = str(col).lower(); sk = norm_state(row["state2"])
        for key, dist in NMC_PG_COLLEGE_OVERRIDE.items():         # (1) PG-only institute override
            if key in cl:
                dc, _, _ = resolve(row["state2"], dist)
                if dc is not None:
                    audit.append({"college": col, "state": row["state2"],
                                  "assigned_lgd_district": dist, "method": "override",
                                  "pg_seats": int(row["seats"])})
                    return _sid(dc), "override"
        ck = norm(col)
        if ck in gmap:                                            # (2) exact UG college-name join
            return gmap[ck], "ug_exact"
        keys = list(smap.get(sk, {}).keys())                     # (3) fuzzy UG join within state
        if keys:
            m = process.extractOne(ck, keys, scorer=fuzz.token_sort_ratio)
            if m and m[1] >= 88:
                return smap[sk][m[0]], "ug_fuzzy"
        parts = [p.strip() for p in str(col).split(",") if p.strip()]   # (4) comma-town fallback
        town = parts[-1] if parts else col
        dc, meth, _ = resolve(row["state2"], town)
        if dc is not None:
            return _sid(dc), "town_" + meth
        return None, "unmatched"
    recs = agg.apply(place, axis=1, result_type="expand")
    agg["district_code"], agg["_method"] = recs[0], recs[1]

    ok = agg[agg["district_code"].notna()].copy()
    for _, r in ok.iterrows():
        NAMEMAP.append({"source": "NMC_PG", "src_state": r["state2"], "src_district": r["College"],
                        "district_code": r["district_code"],
                        "lgd_district": CODE_NAME.get(r["district_code"]),
                        "lgd_state": CODE_STATE.get(r["district_code"]),
                        "method": r["_method"], "score": ""})
    for _, r in agg[agg["district_code"].isna()].iterrows():
        UNMATCHED.append({"source": "NMC_PG", "src_state": r["state2"], "src_district": r["College"],
                          "reason": "unmatched", "best_fuzzy_score": "", "seats": r["seats"]})
    if audit:
        pd.DataFrame(audit).sort_values(["state", "assigned_lgd_district"]).to_csv(
            os.path.join(CW, "nmc_pg_manual_placements.csv"), index=False)

    g = ok.groupby("district_code").agg(pg_seats=("seats", "sum"),
                                        pg_college_count=("colleges", "sum"),
                                        pg_superspecialty_seats=("sup_seats", "sum"))
    vcols = ["pg_seats", "pg_college_count", "pg_superspecialty_seats"]
    out = SPINE[["district_code", "district_name_english", "state_name_english"]].merge(
        g, on="district_code", how="left")
    out[vcols] = out[vcols].fillna(0)
    out.to_csv(os.path.join(CW, "nmc_pg_by_district.csv"), index=False)
    return out

# ======================================================================================
# 7 | Source: RBI Bank Outlets (current-vintage; apportion any missing current district)
# ======================================================================================
def build_rbi_outlets():
    df = pd.read_csv(os.path.join(RAW, "economic_financial_access",
                                  "RBI_Bank_Outlets_by_district_2026-07-08.csv"), dtype=str)
    vcols = ["Total_Bank_Outlets", "Public_Sector", "Private_Sector", "Regional_Rural",
             "Small_Finance", "Foreign", "Local_Area", "Payments_Banks",
             "Rural", "SemiUrban", "Urban", "Metropolitan"]
    matched = match_frame(df, "State", "District", "RBI_Outlets", vcols)
    out = to_spine(matched, vcols)
    out = apportion(out, vcols)
    out.columns = [c.lower() if c in vcols else c for c in out.columns]
    out = SPINE[["district_code", "district_name_english", "state_name_english"]].merge(
        out, on="district_code", how="left")
    out.to_csv(os.path.join(CW, "rbi_bank_outlets.csv"), index=False)
    return out

# ======================================================================================
# 8 | Source: RBI Statement 4A -- deposits / credit / reporting offices (4 annual periods)
# ======================================================================================
def build_stmt4a():
    f = os.path.join(RAW, "economic_financial_access",
                     "RBI_Statement4A_District_Deposits_Credit_Annual_inclRRB_2023-2026.xlsx")
    raw = pd.read_excel(f, header=None)
    periods = [("2025_26", 4), ("2024_25", 7), ("2023_24", 10), ("2022_23", 13)]
    body = raw.iloc[7:].copy()
    long_rows = []
    for _, r in body.iterrows():
        state, dist = r[2], r[3]
        if pd.isna(dist):
            continue
        for pname, c in periods:
            long_rows.append({"state": state, "district": dist, "period": pname,
                              "n_offices": r[c], "deposits_cr": r[c + 1], "credit_cr": r[c + 2]})
    lf = pd.DataFrame(long_rows)
    vcols = ["n_offices", "deposits_cr", "credit_cr"]
    order = [p for p, _ in periods]
    # pass 1: match + spine every period WITHOUT apportionment -- the split below needs the
    # whole panel first, because it reads a district's own values from its other periods.
    spined = {}
    for pname in order:
        sub = lf[lf["period"] == pname]
        matched = match_frame(sub, "state", "district", f"RBI_4A_{pname}", vcols)
        spined[pname] = to_spine(matched, vcols).set_index("district_code")

    # pass 2: PANEL-AWARE forward-split. 4A's district set tracks the reorgs year by year
    # (Dudu has rows only at Mar-2024/Mar-2025 -- created Aug-2023, abolished Dec-2024;
    # Jodhpur Rural is back inside Jodhpur's row by 2025-26), so a family member absent in
    # a period is reported INSIDE its head's row that year. The generic apportion() cannot
    # handle this: it skips any family where some member is present, which left e.g.
    # Jodhpur Rural's real Rs 6,284 cr at 0 and made CAGRs compare different territories.
    # Here each absent member is carved OUT of its head's lump at the member's nearest
    # OBSERVED year's own value (real data beats a population share: Dudu's observed level
    # is ~1% of the family, its population share ~7%); only never-observed members split
    # the remainder by 2011 population.
    name_code = dict(zip(SPINE["district_name_english"], SPINE["district_code"]))
    # Multi-parent carve overrides. The single-parent family map routes each child's whole
    # carve to ONE head, but 4A's own rows show these two drew from BOTH parents -- the
    # creation-year drops and the abolition-year jumps in the parents' rows split the same
    # way, to within organic deposit growth (Gangapur City: SM -1,904/Karauli -396 on entry,
    # +3,911/+1,694 on exit; Neem Ka Thana: Sikar/Jhunjhunu 2:1 on both events). Without
    # this, Karauli's real Rs 6,606 cr row loses the whole Rs 4,239 cr carve.
    multi_donor = {
        name_code["Gangapurcity"]: {name_code["Sawai Madhopur"]: 0.75,
                                    name_code["Karauli"]: 0.25},
        name_code["Neem Ka Thana"]: {name_code["Sikar"]: 2 / 3,
                                     name_code["Jhunjhunu"]: 1 / 3},
        # Beawar: family head is Pali, but on creation Ajmer dropped ~5.5k organic-adjusted
        # vs Pali ~1.1k and Rajsamand ~0 -- Beawar city itself was Ajmer district.
        name_code["Beawar"]: {name_code["Ajmer"]: 0.8, name_code["Pali"]: 0.2},
        # Kotputli-Behror: Kotputli tehsil was Jaipur's, Behror/Tijara belt Alwar's; Alwar's
        # organic-adjusted creation drop (~14.6k) covers Khairthal-Tijara (10.4k) plus only
        # about half of K-B -- the other half must have left Jaipur's (much noisier) row.
        name_code["Kotputli-Behror"]: {name_code["Alwar"]: 0.5, name_code["Jaipur"]: 0.5},
        # 2025-26 folds Mahe and Yanam into the PUDUCHERRY district row (3,970 Karaikal +
        # 32,517 Puducherry = the UT's 36,487 total exactly); they are solo families, so the
        # family loop cannot see them -- carve both at their last observed values.
        name_code["Mahe"]: {name_code["Puducherry"]: 1.0},
        name_code["Yanam"]: {name_code["Puducherry"]: 1.0},
    }
    panels = []
    for pname in order:
        out = spined[pname].copy()
        has = out[vcols].notna().any(axis=1)
        flag = pd.Series(False, index=out.index)
        near = sorted((q for q in order if q != pname),
                      key=lambda q: abs(order.index(q) - order.index(pname)))
        for child, donors in multi_donor.items():
            if has.get(child, False) or not all(has.get(d, False) for d in donors):
                continue
            vv = None
            for q in near:
                qrow = spined[q].loc[child, vcols]
                if qrow.notna().any():
                    vv = qrow.astype(float).fillna(0.0)
                    break
            if vv is None:
                continue
            out.loc[child, vcols] = vv.values
            has.loc[child] = True                     # family loop must see it as present
            for d, w in donors.items():
                out.loc[d, vcols] = (out.loc[d, vcols].astype(float) - vv * w).values
            flag.loc[[child] + list(donors)] = True
        for fam, members in FAM_MEMBERS.items():
            head = FAM_HEAD.get(fam)
            if len(members) < 2 or head is None or not has.get(head, False):
                continue
            absent = [m for m in members if not has.get(m, False)]
            if not absent:
                continue
            lump = out.loc[head, vcols].astype(float)
            carried = {}
            for m in absent:
                for q in near:
                    qrow = spined[q].loc[m, vcols]
                    if qrow.notna().any():
                        carried[m] = qrow.astype(float).fillna(0.0)
                        break
            rem = lump.copy()
            for vv in carried.values():
                rem = rem - vv
            rest = [head] + [m for m in absent if m not in carried]
            if (rem < 0).any():          # carried values exceed the lump: distrust them
                carried, rem, rest = {}, lump.copy(), [head] + absent
            w = np.array([POP.get(m, 0.0) for m in rest], dtype=float)
            w = np.ones(len(rest)) / len(rest) if w.sum() <= 0 else w / w.sum()
            for m, vv in carried.items():
                out.loc[m, vcols] = vv.values
            for m, wi in zip(rest, w):
                out.loc[m, vcols] = (rem * wi).values
            flag.loc[[head] + absent] = True
        out = out.reset_index()
        out["is_apportioned"] = flag.values
        out = redistribute_lumped_state(out, "delhi", vcols)  # 4A reports NCT as one unit
        out["period"] = pname
        panels.append(out)
    panel = pd.concat(panels, ignore_index=True)
    panel["cd_ratio_pct"] = 100 * panel["credit_cr"] / panel["deposits_cr"]
    panel["pop2011"] = panel["district_code"].map(POP)
    panel["deposits_per_capita_rs"] = panel["deposits_cr"] * 1e7 / panel["pop2011"].replace(0, np.nan)
    panel = SPINE[["district_code", "district_name_english", "state_name_english"]].merge(
        panel, on="district_code", how="right")
    panel.to_csv(os.path.join(CW, "rbi_deposits_credit_panel.csv"), index=False)

    # latest + 3-year CAGR (2022_23 -> 2025_26)
    lat = panel[panel["period"] == "2025_26"].copy()
    old = panel[panel["period"] == "2022_23"].set_index("district_code")
    for m in ["deposits_cr", "credit_cr"]:
        r = lat.set_index("district_code")[m] / old[m].replace(0, np.nan)
        lat[m.replace("_cr", "") + "_cagr_3y"] = (np.power(r, 1 / 3) - 1).reindex(
            lat["district_code"]).values
    lat.to_csv(os.path.join(CW, "rbi_deposits_credit_latest.csv"), index=False)
    return panel, lat

# ======================================================================================
# 9 | Supplementary single-state source (NABARD-WB) -> LGD-keyed   [SLBC-MP retired, superseded by 4A]
# ======================================================================================
def build_supplementary():
    # SLBC-MP retired 2026-07-15: fully superseded by RBI Statement 4A, which now carries MP's
    # districts (deposits / credit / CD ratio) all-India at the same 2025 vintage. Its raw inputs
    # and old slbc_mp.csv output were retired out of the live tree. NABARD-WB stays live -- it has
    # FI sub-indices (availability / outreach / usage per capita) that 4A does NOT cover.
    wb = pd.read_csv(os.path.join(RAW, "economic_financial_access",
                                  "NABARD_WB_FI_composite_index_2022-23.csv"), dtype=str)
    wb["state"] = "West Bengal"
    vc = [c for c in wb.columns if c not in ("District", "state")]
    w = match_frame(wb, "state", "District", "NABARD_WB", vc)
    w = SPINE[["district_code", "district_name_english"]].merge(w, on="district_code", how="right")
    w.to_csv(os.path.join(CW, "nabard_wb.csv"), index=False)

# ======================================================================================
# 10 | Assemble consolidated 785-district wide table + reports
# ======================================================================================
def main():
    rhs_panel, y19 = build_rhs()
    nmc = build_nmc()
    nmc_pg = build_nmc_pg()
    outlets = build_rbi_outlets()
    s4a_panel, s4a_lat = build_stmt4a()
    build_supplementary()

    C = SPINE[["district_code", "district_name_english", "state_name_english",
               "is_post2011", "parent_district_name", "pop2011"]].copy()
    C = C.merge(y19[["district_code", "sub_centres_incl_hwc", "phcs_incl_hwc",
                     "chcs", "sub_divisional_hospitals", "district_hospitals",
                     "is_apportioned"]].rename(columns={"is_apportioned": "rhs2019_apportioned"}),
                on="district_code", how="left")
    C = C.merge(nmc[["district_code", "mbbs_college_count", "mbbs_seats"]],
                on="district_code", how="left")
    C = C.merge(nmc_pg[["district_code", "pg_college_count", "pg_seats",
                        "pg_superspecialty_seats"]],
                on="district_code", how="left")
    C = C.merge(outlets[["district_code", "total_bank_outlets", "public_sector",
                         "private_sector", "regional_rural", "rural", "urban", "metropolitan"]],
                on="district_code", how="left")
    C = C.merge(s4a_lat[["district_code", "n_offices", "deposits_cr", "credit_cr",
                         "cd_ratio_pct", "deposits_per_capita_rs",
                         "deposits_cagr_3y", "credit_cagr_3y"]],
                on="district_code", how="left")
    C.to_csv(os.path.join(CW, "consolidated_by_district.csv"), index=False)

    pd.DataFrame(NAMEMAP).to_csv(os.path.join(CW, "name_map.csv"), index=False)
    pd.DataFrame(UNMATCHED).to_csv(os.path.join(CW, "unmatched_report.csv"), index=False)

    # -------- console summary --------
    nm = pd.DataFrame(NAMEMAP); um = pd.DataFrame(UNMATCHED)
    print("\n================  CROSSWALK BUILD SUMMARY  ================")
    print(f"LGD spine: {len(SPINE)} current districts ({SPINE['is_post2011'].sum()} post-2011)")
    print("\nResolved rows per source (method mix):")
    for src, g in nm.groupby("source"):
        print(f"  {src:16s} matched {len(g):4d} | " +
              ", ".join(f"{k}={v}" for k, v in g['method'].value_counts().items()))
    if len(um):
        print("\nUnresolved rows per source (reason):")
        for src, g in um.groupby("source"):
            print(f"  {src:16s} {len(g):3d} | " +
                  ", ".join(f"{k}={v}" for k, v in g['reason'].value_counts().items()))
    # coverage: districts with each headline var present
    print("\nConsolidated coverage (districts with >0 value / 785):")
    for col in ["sub_centres_incl_hwc", "mbbs_seats", "pg_seats", "total_bank_outlets", "deposits_cr"]:
        present = (pd.to_numeric(C[col], errors="coerce").fillna(0) > 0).sum()
        print(f"  {col:24s} {present:4d}")
    # NMC UG + PG seats placed vs unplaced
    if len(um):
        nmc_un = um[um["source"] == "NMC_UG"]
        if len(nmc_un):
            unplaced = pd.to_numeric(nmc_un.get("seats"), errors="coerce").sum()
            print(f"\nNMC UG seats unplaced (in unmatched report): {unplaced:.0f} of "
                  f"{pd.to_numeric(pd.read_csv(os.path.join(RAW,'medical_education_tertiary','NMC_UG_Seat_Matrix_2024-25.csv'))['AnnualIntake'],errors='coerce').sum():.0f}")
    pg_src = pd.to_numeric(pd.read_csv(os.path.join(RAW, 'medical_education_tertiary',
                          'NMC_PG_Seat_Matrix_2024-25.csv'))['Seats'], errors='coerce').sum()
    pg_placed = pd.to_numeric(C['pg_seats'], errors='coerce').sum()
    print(f"NMC PG seats placed: {pg_placed:.0f} of {pg_src:.0f} "
          f"({100*pg_placed/pg_src:.1f}%); unplaced {pg_src-pg_placed:.0f}")
    print(f"\nApportioned districts (RHS 2019): {int(C['rhs2019_apportioned'].fillna(False).sum())}")
    print("Outputs written to data/processed/crosswalk/  (see file list in module docstring).")
    print("==========================================================\n")

if __name__ == "__main__":
    main()
