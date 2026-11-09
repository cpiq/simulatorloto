# Loto 6/49: Python standalone in VS Code (optimizare 4+)

Acest script poate rula independent, fara Flask, JavaScript sau ChatGPT Sites.

**Fisier:** [loto649_standalone.py](../loto649_standalone.py)

## Instalare (Windows / VS Code)

Selecteaza un interpreter Python 3.10+ in VS Code si deschide terminalul:

```powershell
python -m pip install requests tzdata
python .\loto649_standalone.py --help
```

Daca `python` nu este gasit, foloseste `py -3`.

## Exemple

```powershell
# Motor standard v2: 6 bilete de 6 numere + cate 1 extra
python .\loto649_standalone.py --n 6 --seed 649 --extra 1 --json standard.json

# Varianta pentru obiectivul 4+: 42 de bilete SIMPLE (nu sisteme)
# De sapte ori costul a sase bilete simple.
python .\loto649_standalone.py --focus4 --seed 649 --json focus4.json

# Alternativa: sase sisteme COMPLETE de sapte numere, disjuncte.
# FIECARE sistem 7/6 implica 7 variante platite, deci 42 in total.
python .\loto649_standalone.py --n 6 --extra 1 --extension-strategy disjoint7 --json sisteme7.json

# Walk-forward pe ultimele 500 de extrageri, comparatie cu random la buget egal
python .\loto649_standalone.py --focus4 --backtest 500 --json backtest4.json

# Complet offline, cu istoric local:
python .\loto649_standalone.py --focus4 --history-json istoric.json --json offline.json
```

**Input istoric JSON**, de forma:

```json
[
  {"date": "2026-11-05", "numbers": [2, 18, 22, 29, 33, 38]},
  {"date": "2026-11-08", "numbers": [3, 13, 20, 41, 48, 49]}
]
```

La prima generare bazata pe istoric local se solicita cel putin 30 de extrageri.
Fara `--history-json`, scriptul descarca si verifica toate cele sapte surse
ale arhivei `loto49.ro`. Pentru backtest sunt necesare cel putin 261 extrageri.
Daca o sursa externa nu poate fi verificata, programul se opreste, nu foloseste
date istorice inventate. Verifica mereu data `history.last_date` din JSON.

## Sanse teoretice pentru minimum 4 numere

Pentru o extragere independenta echiprobabila de 6/49:

| Joc complet platit | Numar variante simple | Probabilitate de >=4 intr-o extragere |
|---|---:|---:|
| 6 bilete simple disjuncte | 6 | ~0.5923% |
| 6 sisteme complete 7/6 disjuncte | 42 | ~1.3311% |
| 42 bilete simple, toate cu intersectie <=1 | 42 | ~4.1460% |

Calculele sunt exacte pentru aceste configuratii (evenimentele de 4+
sunt disjuncte); nu reprezinta succes istoric si nu garanteaza rezultate.
Sistemele reduse din UI nu sunt echivalente cu sistemul **complet** de 7 numere.

`--focus4` optimizeaza matematic **acoperirea** la un buget de 42 variante,
nu predictia numerelor. Daca bugetul ramane 6 bilete simple, structura
curenta v2 cu sase bilete disjuncte atinge deja limita teoretica la 4+.
Biletele se schimba doar cand se schimba data ultimei extrageri istorice
sau seed-ul, conform motorului `649-coverage-v2`.

## Teste

```powershell
python -m pip install flask numpy beautifulsoup4 scipy
python -m unittest discover -s tests -v
```

Testele contin verificari pentru paritatea motorului standard cu referinta
Sites v25, reproducibilitate, diversificarea biletelor si executia standalone
cu `--focus4` / `disjoint7`.

**Atentie:** modificarile la codul din GitHub nu publica automat o noua
versiune in ChatGPT Sites. Versiunea existenta din Sites nu a fost editata aici.
