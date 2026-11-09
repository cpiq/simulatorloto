# Loto 6/49 - Python standalone in VS Code

Sursa: `loto649_standalone.py`. Ruleaza fara Flask, browser, API de plata sau aplicatia Sites.
Descarca numai arhiva istorica de pe `loto49.ro` (daca nu primeste `--history-json`).

## Pornire Windows, terminal VS Code

1. Descarca `loto649_standalone.py` din acest branch si deschide folderul in VS Code.
2. In terminal (PowerShell):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install requests tzdata
```

3. Genereaza variantele simple implicite (6 bilete):

```powershell
.\.venv\Scripts\python.exe .\loto649_standalone.py --n 6 --extra 0 --json bilete_6.json
```

4. Mod de **acoperire 4+**: 42 de variante SIMPLE (de 7 ori mai multe decat cele 6 simple), configurate cu maximum un numar comun intre doua bilete:

```powershell
.\.venv\Scripts\python.exe .\loto649_standalone.py --focus4 --json bilete_focus4.json
```

5. Mod alternativ: sase sisteme COMPLETE de 7 numere, cu 42 variante simple platite in total. Grupele de cate 7 sunt disjuncte, dar au **sanse mai mici de 4+** decat 42 simple la acelasi buget:

```powershell
.\.venv\Scripts\python.exe .\loto649_standalone.py --n 6 --extra 1 --extension-strategy disjoint7 --json sisteme_7.json
```

6. Verificare pe numere extrase, numai pentru inspectie (NU este dovada unei predictii facute inaintea extragerii):

```powershell
.\.venv\Scripts\python.exe .\loto649_standalone.py --focus4 --verify-draw "3,13,20,41,48,49" --json verificare.json
```

7. Backtest cronologic (fara date viitoare in modelul fiecarei extrageri):

```powershell
.\.venv\Scripts\python.exe .\loto649_standalone.py --focus4 --backtest 200 --json backtest_200.json
```

Pentru comparatii auditabile, salveaza fisierele JSON inainte de viitoarele extrageri, inclusiv `history.last_date`, `history.sha256`, `seed` si variantele. Nu regenera retroactiv si nu trata seturi create dupa extragere ca predictii.

## Probabilitati exacte (Loto 6/49 uniform)

| Pachet | Variante simple platite | Probabilitate cel putin un 4/6 sau mai bine |
|---|---:|---:|
| 6 simple diversificate | 6 | ~0.5923% |
| 6 sisteme complete disjuncte de 7 numere | 42 | ~1.3311% |
| 42 simple cu maximum un numar comun intre oricare doua | 42 | ~4.1460% |

Acestea sunt probabilitati *per extragere*, nu promisiuni de castig. Motorul genereaza combinatii pentru o loterie cu extrageri independente, deci 4/6 nu poate fi garantat. O crestere a numarului de variante platite mareste proportional bugetul si expunerea financiara; nu reprezinta un avantaj predictiv.

## Detalii tehnice

- Generator reproductibil: `649-coverage-v2`, seed implicit 649, ciclu fixat de data ultimei extrageri din istoric.
- `--focus4`: modifica numai N=42, extra=0. Primele 8 variante simple sunt complet disjuncte; restul au suprapuneri de maximum un numar.
- `--extension-strategy disjoint7` este optional; generatorul implicit ramane neschimbat, pentru compatibilitate cu versiunea Sites si fixture-urile JS v25.
- `--history-json istoric.json` permite utilizarea unui istoric local. Formatul este lista `[{"date":"2026-11-08","numbers":[3,13,20,41,48,49]}, ...]`; pentru analiza este necesar istoric valid cu cel putin 30 extrageri.
- `--verify-draw` verifica pachetul curent fata de o extragere furnizata explicit. Pentru comparatie corecta cu predictiile trecute, foloseste JSON-urile salvate *anterior*, nu generarea noua.
- Aplicatia ChatGPT Sites nu este modificata prin GitHub. Codul GitHub descrie varianta standalone/API din repository, nu certifica automat implementarea activa din Sites.

## Teste repository

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
