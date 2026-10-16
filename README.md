# RedFear Support

Bot Discord în română pentru un singur server, Python 3.11+, discord.py, SQLite. O singură instanță activă.

## Funcții

- Tichete numerotate `tichet-0001`, contor persistent. Numerele pot avea goluri după cereri eșuate; nu se reutilizează.
- Ajutor, Reclamație, Contestație, Bug: formulare diferite, fără cerere de parole/tokenuri.
- Categorii private separate, create prin `/ticket_categories`. Până la configurare se folosește TICKET_CATEGORY_ID existent.
- Maximum un tichet activ per autor; pauză de 60 secunde între creări.
- Notificare autor și roluri de suport la deschidere, fără @everyone/@here. Pentru ping real rolurile trebuie să fie mentionable SAU botul să aibă Mention Everyone. Recomandare: acordă această permisiune doar rolului botului; codul permite exclusiv menționarea ID-urilor de suport configurate. Nu activa menționarea rolurilor de staff pentru toți membrii dacă nu dorești asta.
- Preluare exclusivă, eliberare și transfer către un coleg de suport; adăugare/eliminare participant (maximum 10); redenumire. Controale validate din nou la trimiterea formularelor.
- Închidere prin formular cu motiv obligatoriu, export HTML în arhiva privată, eliminarea accesului autorului și participanților. Canalul rămâne pentru staff.
- Redeschidere de către staff cu restabilirea autorului/participanților rămași pe server și resetarea preluării. Refuzată dacă autorul are alt tichet activ sau a părăsit serverul.
- Statistici deschise/închise/total, preluări și închideri per actor. Închiderile efectuate de autor sunt incluse; nu sunt toate rezolvări de moderator. Redeschiderile păstrează istoricul; închiderea repetată după redeschidere contează ca eveniment nou.
- Panoul și butoanele funcționează după restart; migrare automată a bazei vechi fără ștergerea tichetelor. Vechile canale nu sunt redenumite până la o acțiune ulterioară.

## Discord: configurare

Developer Portal → Bot: Message Content Intent activ; Presence și Members nu sunt necesare. OAuth2 scopes: `bot`, `applications.commands`.

Permisiuni în **rolul botului din server**: View Channels, Manage Channels, Manage Roles, Send Messages, Embed Links, Attach Files, Read Message History. Opțional Mention Everyone pentru pingurile rolurilor de suport. Administrator nu este necesar. Bifele din calculatorul Developer Portal nu actualizează rolul existent.

În categoria TICHETE și în #ajutor permite botului vizualizare/scriere/embed/fișiere/istoric. În categorie permite și Manage Channels / Manage Permissions. Codul nu mai încearcă să atribuie Manage Roles explicit la crearea canalului (corecția erorii 50013).

Arhiva #arhiva-tichete trebuie privată: numai suportul, administratorii și botul. Botul verifică accesul rolurilor și excepțiile membrilor individuali înainte de export. Verifică manual și conturile cu acces administrativ. SUPPORT_ROLE_IDS trebuie să conțină numai Moderator/Helper, niciodată @everyone sau Membru.

În #ajutor permite membrilor verificați să vadă panoul. Categoria și tichetele de suport nu trebuie făcute publice. Un utilizator cu Administrator poate vedea inclusiv tichetele închise.

## Instalare nouă pe Linux

Clonează repository-ul privat cu autentificarea GitHub proprie. Din checkout:

```bash
sudo apt install python3 python3-venv git
sudo python3 deploy/setup.py
sudo journalctl -u redfear-support -n 40 --no-pager
```

Scriptul cere tokenul prin introducere ascunsă și ID-urile Discord. Creează utilizatorul `redfear`, instalează în `/opt/redfear-support`, păstrează `.env` cu mod 600 și configurează serviciul systemd. Nu publica tokenul, `.env` sau `data/`.

## Actualizare instalare existentă

Din checkout-ul din home, **nu** din `/opt/redfear-support`:

```bash
cd ~/RedFear-Support
git pull --ff-only origin main
sudo python3 deploy/update.py
sudo journalctl -u redfear-support -n 40 --no-pager
```

Pentru o ramură de PR încă nemerged, folosește `git fetch origin` și `git switch --track origin/feat/support-v2` în locul pull-ului de mai sus. După merge revino cu `git switch main` și `git pull --ff-only origin main`.

Actualizatorul verifică sintaxa, oprește serviciul, salvează codul și SQLite în `/opt/redfear-support/backups/`, copiază fișierele noi și pornește serviciul. Nu modifică `.env` și nu recreează baza de date. Dependențele nu s-au schimbat în această versiune. Confirmă mesajul `Conectat` în log: pornirea systemd singură nu dovedește conectarea la Discord. Nu rula simultan `bot.py` manual.

În Discord, după actualizare:

1. Administratorul execută **`/ticket_categories`** o singură dată. Creează patru categorii private și salvează ID-urile în SQLite. Repetarea reutilizează ID-urile salvate; nu adoptă categorii existente doar după nume. Nu mută tichetele vechi.
2. Panoul existent funcționează în continuare. Nu publica unul nou dacă nu ai nevoie de un al doilea panou.
3. Stafful poate executa **`/ticket_controls`** într-un tichet vechi pentru noile butoane, inclusiv redeschiderea celor închise anterior.
4. **`/ticket_stats`** afișează raportul privat pentru staff. Setările implicite Discord permit comenzile de staff membrilor cu Manage Channels; administratorul poate permite explicit și rolul Helper din Server Settings → Integrations → RedFear Support. Codul verifică și rolurile de suport la execuție.

## Administrare, feedback și automatizări

- **Preluare exclusivă:** după preluare, numai colegul desemnat sau un administrator poate administra/închide tichetul ca staff. Autorul îl poate închide în continuare. Vizualizarea și scrierea celorlalți membri staff rămân disponibile pentru colaborare. **Eliberează** permite altui coleg să preia; **Transferă** cere ID-ul unui moderator/helper valid și îl notifică. Nu modifică roluri Discord.
- **Prioritate:** meniul din tichet oferă Normală / Ridicată / Urgentă. Alegerea este salvată în SQLite, în topic și într-un mesaj embed colorat. Prioritatea este inclusă și în transcriere. Nu promite un timp de răspuns.
- **`/ticket_block member reason hours`** suspendă crearea și redeschiderea pentru un membru obișnuit; `hours=0` înseamnă până la deblocare. **`/ticket_unblock member`** ridică suspendarea. Stafful/boții nu pot fi blocați astfel. Tichetele deja deschise nu sunt închise și nu pierd accesul autorului.
- **Feedback 1–5 stele:** după închidere autorul primește un DM. O evaluare per închidere, disponibilă 30 de zile, validată după ID-ul autorului și ciclul închiderii. Redeschiderea invalidează formularul vechi; evaluările deja înregistrate rămân în istoric. Dacă DM-urile sunt închise, închiderea tichetului reușește și situația apare în log. Nu trimite conversația sau motivul prin DM. `/ticket_stats` include mediile și numărul evaluărilor pe moderatorul desemnat; tichetele fără preluare apar ca neatribuite.
- **`/ticket_health`** pentru staff: permisiuni server/categorii/arhivă, vizibilitatea arhivei, posibilitatea pingului, integritatea SQLite, spațiu pe disc și ultimul backup. Raportul este privat; nu conține tokenul.
- **Backup automat:** la pornire dacă nu există o copie recentă, apoi aproximativ la șase ore, în `data/backups/`. Folosește API-ul SQLite backup pe conexiune separată și verifică integritatea înainte de rotație. Păstrează șapte copii, fișiere mod 600, director mod 700; nu șterge fișiere cu alte nume. Copiile sunt locale pe același VPS, nu protejează de pierderea completă a discului. **`/ticket_backup`**, numai pentru administratori, declanșează o copie imediată. Nu o postează în Discord.
- **Inactivitate:** se verifică aproximativ la fiecare minut. Implicit se trimite o avertizare după 24 de ore fără mesaje umane, iar **închiderea automată este oprită**. Mesajele boților nu resetează timpul. Înainte de decizie, istoricul este verificat pentru mesaje umane ratate cât botul era oprit. Nu este activitate simpla deschidere/citire a canalului.

Configurare de administrator:

```text
/ticket_idle warn_hours:24 close_hours:48
```

Exemplul avertizează după 24h și poate închide după 48h fără mesaje umane, dar lasă întotdeauna minimum 24h de la avertizarea trimisă. Un tichet vechi neavertizat primește întâi avertizarea, inclusiv după restart. Orice mesaj uman nou anulează avertizarea și resetează timpul. Modificarea configurației resetează avertizările pentru a acorda noul interval complet. Închiderea automată folosește aceeași arhivare și verificare de confidențialitate ca închiderea manuală; la eroare se reîncearcă și se scrie în log.

Pentru dezactivarea închiderii automate:

```text
/ticket_idle warn_hours:24 close_hours:0
```

Nu există un timer extern: acestea rulează numai când serviciul botului este pornit și conectat la Discord. Cât botul este oprit, nu se trimit avertizări și nu se închid tichete. Backupul și verificările nu înlocuiesc monitorizarea VPS-ului.

## Validare înainte de utilizare

Testează cu un membru obișnuit și un moderator: formular pentru fiecare tip, categorie corectă, ping staff, lipsa accesului altor membri, blocarea unui al doilea tichet, preluare, adăugare/eliminare participant și redenumire. Repornește serviciul și verifică panoul/butoanele existente. Închide cu motiv: descarcă HTML și verifică mesajele și motivul, precum și lipsa accesului autorului/participanților. Redeschide și verifică accesul, preluarea resetată și statisticile. Un membru obișnuit nu trebuie să poată folosi controalele de staff. Testează și eliberarea/transferul între doi colegi, prioritatea, suspendarea/deblocarea, feedbackul din DM și `/ticket_health`. Verifică existența unei copii în `data/backups/`. Închiderea automată rămâne oprită până configurezi intenționat duratele.

## Arhive, backup și limite

HTML este static, cu text și embed-uri escapate, fără scripturi sau imagini externe. Mesajele sunt exportate pe părți de aproximativ 1 MB. Descărcați fișierul pentru vizualizare în browser dacă Discord nu îl previzualizează. Atașamentele sunt **linkuri**, nu copii permanente; pot expira. Mesajele șterse și versiunile anterioare nu sunt recuperate.

La eroare de export se încearcă restaurarea permisiunilor inițiale și tichetul rămâne deschis pentru retry. Dacă și restaurarea eșuează, verificați logul și permisiunile canalului. Exporturile parțiale pot rămâne în arhivă și pot fi duplicate la retry. Stafful poate scrie în timpul exportului; acesta nu reprezintă un snapshot atomic. Închiderea permite și autorului să dea motiv; nu se pretinde că orice închidere este o rezolvare de moderator.

Canalele închise și backupurile de dinaintea actualizărilor nu se șterg automat. Backupurile automate SQLite au retenție de șapte copii. Stabiliți retenția, curățați manual și monitorizați limitele Discord de canale/categorii. O oprire bruscă între apelurile Discord și salvarea SQLite poate necesita reconciliere manuală (canal rămas fără înregistrare sau stare de acces nealiniată). Botul nu are tranzacții distribuite cu Discord.

Backupul dinaintea actualizării se păstrează pentru recuperare manuală cu serviciul oprit. Nu restaura baza veche după activitate nouă fără evaluarea pierderii acelui istoric. Tokenul nu este inclus în backup; păstrează-l separat în siguranță.

## Teste locale

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q bot.py store.py transcript.py operations.py deploy
```

Testele folosesc SQLite real și interacțiuni Discord simulate. Nu înlocuiesc validarea pe server și nu folosesc tokenuri.
