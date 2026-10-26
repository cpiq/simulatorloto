# Sincronizare Loto 6/49 — 26 octombrie 2026

Aplicația Python reproduce motorul `649-coverage-v2` din versiunea Sites 25,
publicată pe [simulareloto.ro](https://www.simulareloto.ro/).
Sursa JavaScript de referință are commitul
`844a4e5631b6898b51c5e320282e4f65775d7ad3` în proiectul Sites
`appgprj_6a92dc568b0081919783742cd70bc1f8`.

## Comportament

- Implicit: 6 bilete, fiecare cu 6 numere de bază și 1 extra.
- Cele șase bilete de bază conțin 36 de numere distincte. Primele opt bilete
  nu se suprapun; sunt acceptate până la 50 de bilete.
- Numerele sunt alese uniform. Frecvențele, perechile și profilul istoricului
  sunt descriptive și nu influențează biletele de producție.
- Versiunea motorului, seedul și ultima dată istorică fixează biletele.
  Corectarea sau completarea unor extrageri vechi, cu aceeași ultimă dată,
  nu schimbă biletele ori numerele extra.
- Schimbarea numărului de bilete păstrează primele bilete; schimbarea numărului
  de extra păstrează cele șase numere de bază.
- Arhivele 6/49 sunt identificate prin sursă și tabel. Datele cu lună sau zi
  fără zero sunt normalizate; rezultatele Joker sunt excluse.
- Ruta Flask `/api/predict`, interfața și raportul PDF folosesc același motor
  ca `perplexitymix.py`. Generarea nu are plată sau limită gratuită.

## Verificare

```sh
python -m pip install flask requests beautifulsoup4 numpy tzdata
python -m unittest discover -s tests -v
```

Testele compară exact biletele, numerele extra și acoperirea cu rezultate
produse de JavaScriptul publicat. Fișierul de referință din `tests/fixtures/`
păstrează versiunea, commitul și SHA-256 al sursei JavaScript. Testele API
și de parsare rulează fără acces la rețea.

Pornire locală a aplicației:

```sh
python server.py
```

## Script separat

`loto649_standalone.py` include parserul și motorul într-un singur fișier:

```sh
python -m pip install requests tzdata
python loto649_standalone.py
```

Implicit descarcă toate cele șapte arhive și produce 6 bilete + 1 extra.
Opțiuni: `--n 6 --seed 649 --extra 1 --json rezultat.json`.
`--history-json istoric.json` folosește un istoric local explicit.
Pentru a regenera fișierul după modificări:

```sh
python tools/build_standalone.py
```

Un test verifică sincronizarea fișierului separat cu sursele și îl execută
într-un director gol, fără modulele proiectului.

## Publicare și limite

Acest depozit păstrează aplicația Python/Flask. Nu conține baza de date Sites
și nu importă biletele salvate acolo. Publicarea codului pe GitHub nu pornește
singură o nouă implementare Python în producție; găzduirea Python se gestionează separat.

Versiunea Sites 25 este deja publicată. Această sincronizare nu o înlocuiește.
Nu se afirmă un avantaj predictiv sau o garanție de trei potriviri.
