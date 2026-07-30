# Comandi retry pagine — un processo per riga (profilo/porta dedicati)
# Generato: mef_pagine_log.csv + log testuale

# Q1 C040 — 1 pagine
python download_mef_2025.py --year 2025 --trimestre 1 --materia C040 --pagine 3 --profile-dir .edge_profile_mef_q1 --cdp-port 9224

# Q1 D010 — 1 pagine
python download_mef_2025.py --year 2025 --trimestre 1 --materia D010 --pagine 6 --profile-dir .edge_profile_mef_q1 --cdp-port 9224

# Q1 D040 — 13 pagine
python download_mef_2025.py --year 2025 --trimestre 1 --materia D040 --pagine 21,39,41,44,48,50,54,57,59,63,71,72,73 --profile-dir .edge_profile_mef_q1 --cdp-port 9224

# Q1 E010 — 5 pagine
python download_mef_2025.py --year 2025 --trimestre 1 --materia E010 --pagine 3,5,6,8,10 --profile-dir .edge_profile_mef_q1 --cdp-port 9224

