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

Developer Portal → Bot: **Message Content Intent și Server Members Intent active înainte de restart**. Presence nu este necesar. Fără Server Members Intent, această versiune nu se poate conecta la Gateway. OAuth2 scopes: `bot`, `applications.commands`.

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

## Panou status FiveM

Administratorul configurează panoul în Discord:

```text
/fivem_setup endpoint:IP_PUBLIC:30120 channel:#status-server join_link:https://cfx.re/join/COD
```

Înlocuiește exemplele cu adresa reală și codul de conectare. `endpoint` acceptă IP:port sau domeniu public; fără schemă și fără port se folosește HTTP pe 30120. Pentru un proxy HTTPS, folosește URL-ul de bază, de exemplu `https://play.example.com`. Nu include `/dynamic.json`, parametri, credențiale sau tokenuri. Localhost, LAN și adrese speciale sunt refuzate; rezolvarea DNS verifică de asemenea că adresele sunt publice. Redirecționările HTTP sunt refuzate.

`join_link` acceptă linkul oficial `https://cfx.re/join/COD` sau numai codul. Este opțional la configurare: fără el se afișează comanda F8, iar butonul apare după ce linkul este adăugat prin aceeași comandă. Linkul nu este ghicit din IP. Repetarea configurării în același canal actualizează mesajul existent. Mutarea în alt canal încearcă să dezactiveze panoul vechi; dacă botul nu mai are acces, șterge-l manual.

- Mesajul arată starea endpointului, numărul de jucători/locuri, numele serverului, ora ultimei verificări și butonul de conectare atunci când există linkul cfx.re.
- Aproximativ la 60 secunde se citește numai endpointul public `/dynamic.json` și se editează același mesaj. Nu se descarcă liste de jucători/identificatori și nu se folosește RCON. Maximum 8 secunde per cerere și 64 KB per răspuns.
- **Indisponibil** înseamnă server oprit SAU status inaccesibil (firewall, endpoint blocat, DNS, răspuns invalid). Nu este dovada că jocul este sigur offline. Nu se afișează numere vechi de jucători ca fiind actuale. Butonul poate rămâne disponibil: conectarea la joc poate funcționa chiar dacă endpointul de status este blocat.
- Configurarea și ultimul rezultat rămân în SQLite după restart. `/fivem_status` arată privat ultima verificare, cu ora ei, fără a declanșa cereri suplimentare; respectă accesul utilizatorului la canalul panoului. Dacă botul este oprit, ora rămâne veche și panoul nu se actualizează.
- `/fivem_stop`, numai pentru administratori, oprește panoul și încearcă eliminarea butonului; **nu oprește serverul de joc**.
- Dacă mesajul/canalul este șters, botul nu postează automat înlocuitori la fiecare minut. Rulează `/fivem_setup` din nou pentru recreare. Botul are nevoie de View Channel, Send Messages, Embed Links și Read Message History în canal.

Implementare bazată pe endpointurile publice descrise în [documentația Cfx.re](https://docs.fivem.net/docs/server-manual/proxy-setup/) și câmpurile din [InfoHttpHandler.cpp](https://github.com/citizenfx/fivem/blob/master/code/components/citizen-server-impl/src/InfoHttpHandler.cpp). Nu sunt necesare chei de licență sau tokenuri de jucători.

## Aplicații pentru Helper

Un administrator configurează un canal pentru panoul public și un rol de conducere care evaluează aplicațiile:

```text
/helper_setup panel_channel:#aplicatii-staff reviewer_role:@Moderator cooldown_days:7
```

Botul creează separat **aplicatii-helper**, un canal dedicat privat: acces numai pentru rolul ales, bot și administratorii serverului. Nu folosește automat un canal existent cu același nume. Are nevoie de Manage Channels, Manage Roles pentru permisiunile canalelor, View Channel, Send Messages, Embed Links și Read Message History. Nu seta `manage_roles=True` în overwrite-ul botului: permisiunea trebuie moștenită din rolul său de server.

**Canalul de evaluare este administrat exclusiv de bot:** la configurare, trimitere, evaluare și republicare, acesta înlocuiește toate permisiunile individuale ale camerei. Nu adăuga alți membri/roluri în ea și nu o folosi pentru alte conversații. Administratorii au întotdeauna acces. Schimbarea rolului prin `/helper_setup` actualizează și cardurile existente din acea cameră. Panoul public trebuie să fie în alt canal.

- Butonul deschide cinci întrebări: nume/ID în joc, experiență, disponibilitate, gestionarea unui conflict și motivație. Nu solicită date personale reale sau credențiale.
- Fiecare candidat poate avea o singură aplicație în așteptare. După acceptare/respingere poate reaplica după perioada configurată (1–90 zile, implicit 7). Între trimiteri se aplică minimum un minut, inclusiv dacă publicarea a eșuat.
- În camera privată, conducerea folosește **Acceptă/Respinge** și completează motivul transmis candidatului. Nimeni nu își poate evalua propria aplicație, nici administratorii. Prima decizie este definitivă pentru acea aplicație; apăsările repetate sunt refuzate.
- Rezultatul se salvează înaintea notificării DM. Dacă mesajele private sunt blocate, candidatul poate vedea exclusiv propria stare prin `/helper_status`. **Acceptarea nu acordă automat niciun rol**; conducerea îl acordă manual după discuție.
- `/helper_toggle enabled:False` oprește aplicațiile noi, inclusiv formularele deja deschise. Evaluarea celor existente continuă. `enabled:True` redeschide recrutarea.
- `/helper_republish application_id:1` reface sau actualizează cardul în camera sa privată, dacă mesajul a fost șters. `/helper_resend application_id:1` reîncearcă un DM netrimis; nu retrimite rezultate deja confirmate ca livrate.

Configurația, răspunsurile și deciziile rămân în SQLite și în backupurile sale după restart. Nu se șterg automat: administrația trebuie să stabilească retenția, inclusiv pentru mesajele Discord și copii. O oprire între publicarea Discord și salvarea ID-ului poate necesita `/helper_republish` și curățarea unui card duplicat. Dacă publicarea a eșuat, aplicația este marcată ca eșuată și candidatul poate reîncerca după un minut.

Verifică după instalare cu un membru obișnuit: trimitere, lipsa accesului la camera privată, refuzul unei aplicații duplicate, decizie și DM. Repornește și verifică butoanele existente. Testează și DM blocat cu `/helper_status`.

## Comunitate: prima etapă pentru înlocuirea Carl

Aceste funcții sunt oprite până la configurare. Nu migrează setările/istoricul Carl și nu înlocuiesc încă moderarea sau anti-spamul. Dezactivează funcția echivalentă din Carl numai după verificare, ca să eviți mesaje și roluri duplicate.

**Înainte de actualizare:** Developer Portal → aplicația botului → Bot → Privileged Gateway Intents → activează **Server Members Intent**, salvează. Păstrează Message Content Intent activ pentru tichete. Nu este nevoie de Presence Intent. Botul trebuie să aibă Manage Roles, iar rolul său să fie deasupra rolurilor pe care le acordă.

```text
/welcome_setup channel:#bun-venit message:Bine ai venit în comunitatea RedFear! role:@Membru
/welcome_test
/welcome_toggle enabled:False
/roles_setup channel:#roluri role1:@Evenimente role2:@Update-uri
/community_logs reviewer_role:@Moderator
/community_logs_stop
```

- `welcome_setup` salvează textul exact (maximum 1500 caractere), canalul și opțional rolul automat. Nu există variabile sau cod executat în text; botul adaugă separat mențiunea noului membru, fără pinguri. Se aplică membrilor umani care intră de acum înainte, nu retroactiv tuturor. `welcome_test` trimite numai o previzualizare privată și nu acordă roluri. `welcome_toggle` controlează atât mesajul, cât și rolul automat. Dacă există Membership Screening, rolul așteaptă acceptarea regulilor.
- **Folosește roluri existente când sunt potrivite; nu recrea Helper/Moderator.** Pentru acordare automată și butoane sunt acceptate numai roluri de etichetă/notificări: toate permisiunile de rol oprite, fără permisiuni pozitive în canale, nemanageriate și sub rolul botului. Rolurile de suport și rolul care evaluează aplicațiile sunt refuzate. Membrii moștenesc permisiunile obișnuite din @everyone. Rolurile de staff nu sunt self-service. Configurația și permisiunile sunt reverificate și la utilizare.
- `roles_setup` acceptă 1–5 roluri distincte. Butonul adaugă rolul sau îl elimină dacă membrul îl are deja. În același canal se reutilizează mesajul; mutarea panoului invalidează butoanele celui vechi. Șterge manual panourile vechi din alte canale. La ștergerea mesajului, repetă comanda pentru recreare. Butoanele funcționează după restart.
- `community_logs` creează camera dedicată **redfear-loguri**, fără reutilizarea unui canal găsit doar după nume. Acces: rolul ales, bot și administratori. La publicarea fiecărui lot înlocuiește integral permisiunile camerei; nu o folosi pentru alte conversații și nu adăuga alte roluri/membri acolo. Reconfigurarea reutilizează camera salvată. O cameră ștearsă se recreează numai prin comandă.
- Logurile includ numai intrări/ieșiri și schimbări de roluri (numele membrului, ID și numele rolurilor); nu includ mesaje, atașamente, conversații din tichete, răspunsuri la aplicații sau autorul presupus al unei modificări. Nu reprezintă încă un jurnal de moderare/audit complet. Se trimit în loturi la aproximativ 5 secunde. La lipsa accesului se reîncearcă în aceeași cameră; nu există fallback într-un canal public.
- Coada este limitată la ultimele 200 de evenimente în memorie; evenimentele mai vechi la suprasarcină, cele din timpul opririi sau cele netrimise înainte de restart se pot pierde. Configurația rămâne în SQLite. Logurile Discord nu au ștergere automată. `community_logs_stop` oprește înregistrările noi și golește coada, păstrând camera și mesajele deja publicate.

Verifică cu un membru de test: bun venit, screening, acordare/eliminare rol, lipsa accesului la loguri și funcționarea butoanelor după restart. În canalele publice botul are nevoie de View Channel, Send Messages și Embed Links; pentru reutilizarea mesajelor și Read Message History. Pentru camera privată are nevoie de gestionarea canalelor și a permisiunilor. Rolurile Helper/Moderator și recrutarea rămân separate.

## Organizare canale și creare roluri de notificări

```text
/server_setup
```

Comanda doar citește canalele și afișează un plan privat, cu numele noi și categoriile destinație. Butonul **Aplică organizarea** este rezervat administratorului care a cerut planul și expiră după 3 minute. Nu există modificări înainte de apăsare. La confirmare sunt reverificate administratorul, permisiunea Manage Channels și starea canalelor din plan; dacă numele, categoria, descrierea sau permisiunile unui canal s-au schimbat, generează altă previzualizare.

Se organizează canalele pentru bun venit, regulament, anunțuri, roluri, status FiveM, conectare, actualizări, ajutor și aplicații staff în **INFORMAȚII / FIVEM / SUPORT**. Camerele de evaluare și loguri sunt mutate în **STAFF** numai dacă ID-urile lor sunt deja în configurațiile funcțiilor respective; nu se adoptă camere private doar după nume. Canalele de tichete (inclusiv cele închise), canalul de arhivă și categoriile de tichete sunt protejate.

Canalele sunt identificate întâi prin alegerea explicită, apoi prin configurația funcției, ID-ul salvat de organizator și în final prin nume normalizat unic. De exemplu `👋・bun-venit` și `bun-venit` se potrivesc. Ambiguitățile opresc planul; alegerea explicită se face astfel:

```text
/server_map destination:welcome channel:#canalul-existent
/server_setup
```

`server_map` doar salvează alegerea. Revizuiește ID-urile/mențiunile din plan înainte de confirmare. Nu se șterg canale, mesaje sau categorii vechi. Nu se sincronizează și nu se înlocuiesc permisiunile canalelor existente; accesul lor actual este păstrat chiar și când categoria destinație este diferită. Descrierile canalelor publice indicate în plan sunt înlocuite; descrierile camerelor private sunt păstrate. Organizatorul nu acordă automat acces staffului în canale existente și nu mută tichetele/arhivele. Numele se schimbă, dar ID-urile și panourile configurate rămân valabile.

Pentru a **crea și canalele publice lipsă**, cere explicit:

```text
/server_setup create_missing:True
```

Acestea apar ca **NOU PUBLIC** în previzualizare. Regulamentul, anunțurile, rolurile, bun venit, statusul, ghidul de conectare, actualizările și recrutarea noi sunt numai pentru citire pentru @everyone; ajutorul nou permite scrierea. Botul primește permisiuni de publicare. Canalele create nu publică automat conținut/reguli și nu mută configurările funcțiilor; folosește comenzile specifice pentru panouri. Canalele private sunt create numai prin funcțiile lor, nu de organizator. Nu se creează voice în această etapă.

ID-urile categoriilor și canalelor create sunt salvate după fiecare pas pentru reutilizare. O eroare API poate lăsa organizarea parțial aplicată; rulează din nou previzualizarea pentru continuare, nu șterge resursele salvate. Discord și SQLite nu formează o tranzacție atomică: o oprire exact între creare și salvarea ID-ului poate necesita reconciliere manuală. Nicio copie a mesajelor nu este mutată sau ștearsă.

Pentru rolurile și panoul de notificări, fără configurare manuală:

```text
/roles_create_defaults channel:#roluri
```

Creează **📢 Update-uri** și **🎉 Evenimente** cu toate permisiunile oprite sau reutilizează rolurile salvate/rolurile cu nume echivalent unic, dacă sunt sigure. Rolurile existente nu sunt redenumite și permisiunile lor nu sunt schimbate. Roluri duplicate, de staff, cu acces în canale sau cu privilegii sunt refuzate înainte de creare. Are nevoie de Manage Roles și de View Channel, Send Messages, Embed Links, Read Message History în canal; rolul botului trebuie să fie deasupra rolurilor selectate. Helper/Moderator rămân manuale. La repetare, se reutilizează rolurile și mesajul din același canal. Vezi și `/roles_setup` pentru alegerea manuală a altor roluri.

## Moderare cu istoric

Configurare de administrator, alegând **rolurile existente**:

```text
/moderation_setup helper_role:@Helper moderator_role:@Moderator
```

Nu creează roluri, nu atribuie membri și nu schimbă permisiunile Discord. Helper și Moderator trebuie să fie două roluri distincte, nemanageriate, diferite de @everyone. Dacă moderarea nu este configurată, numai administratorii pot folosi comenzile. Rolurile selectate sunt excluse din acordarea automată și din butoanele de notificări.

| Comandă | Acces și efect |
| --- | --- |
| `/warn member reason` | Helper/Moderator/admin: salvează un avertisment în istoricul privat |
| `/warnings member page` | Helper/Moderator/admin: numai avertismentele salvate cu succes, 5/pagină |
| `/mod_history member page` | Helper/Moderator/admin: toate cazurile și încercările înregistrate, 5/pagină |
| `/mod_case case_id` | Helper/Moderator/admin: detaliile private ale unui caz |
| `/timeout member minutes reason` | Moderator/admin + Moderate Members: 1–40320 minute |
| `/untimeout member reason` | Moderator/admin + Moderate Members: elimină timeoutul |
| `/kick member reason` | Moderator/admin + Kick Members: cere confirmare privată |
| `/ban member reason` | Moderator/admin + Ban Members: cere confirmare, **fără ștergerea mesajelor** |
| `/mod_log_retry case_id` | Administrator: retrimite doar logul unui caz nelivrat, fără repetarea sancțiunii |

Motive obligatorii de 3–500 caractere. Rezultatele și istoricul sunt private pentru staff; avertismentul este o înregistrare internă, nu trimite automat DM candidatului. Moderatorii trebuie să aibă și permisiunile Discord native necesare; botul trebuie să le aibă separat. Pentru avertismente nu este nevoie de Moderate Members. Rolul staffului trebuie să fie deasupra rolului țintei (excepție proprietarul serverului), iar pentru sancțiuni native și rolul botului trebuie să fie deasupra. Propria persoană, boții, proprietarul și administratorii sunt protejați. Nu se acordă automat sancțiuni după un anumit număr de avertismente.

Kick/ban cer un buton de confirmare valabil 2 minute și utilizabil numai de autorul cererii. La confirmare se recitesc membrii din Discord și se verifică din nou rolurile, permisiunile și ierarhia. Anularea sau expirarea nu aplică sancțiunea. Sunt acceptați numai membri încă prezenți pentru sancțiuni; istoricul se poate consulta și pentru utilizatori plecați selectabili în Discord. Unban se face momentan din setările Discord.

Cazul este salvat în SQLite **înainte** de apelul Discord. Stări: în curs, aplicat, refuzat, rezultat incert. O întrerupere/restart poate lăsa un caz în curs sau incert; verifică manual în Discord înainte să repeți. Nu se reexecută sancțiuni automat și nu se pretinde tranzacție atomică între Discord și SQLite. Istoricul este numai pentru acțiunile prin acest bot; nu importă sancțiuni Carl sau acțiuni manuale din Discord. Timeoutul expiră nativ în Discord, chiar dacă botul este oprit; expirarea nu creează un caz nou.

Logul detaliat cu membrul, stafful, motivul și starea folosește camera privată din `/community_logs`. Permisiunile camerei sunt reverificate/înlocuite înainte de publicare. Dacă logul e indisponibil sau oprit, cazul rămâne în SQLite și poate fi citit prin `/mod_case`; sancțiunea nu este anulată. `/mod_log_retry` nu retrimite loguri deja confirmate ca livrate. O întrerupere exact după publicarea unui log poate necesita curățarea manuală a unui duplicat. Datele intră în backup și nu au ștergere automată; administrația stabilește retenția.

Nu sunt necesare alte Gateway Intents față de cele deja folosite de bot. Configurează separat Moderate Members/Kick Members/Ban Members pe rolul botului și pe Moderator, numai pentru acțiunile dorite. Nu acorda Administrator Helperilor. Validează pe conturi de test: Helper poate avertiza, nu poate sancționa; membrii obișnuiți nu pot vedea istoricul; o țintă cu rol mai înalt este refuzată. Anti-spamul și blocarea reclamelor nu fac parte din această etapă.

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
python -m compileall -q bot.py store.py transcript.py operations.py fivem.py applications.py community.py server_setup.py moderation.py deploy
```

Testele folosesc SQLite real și interacțiuni Discord simulate. Nu înlocuiesc validarea pe server și nu folosesc tokenuri.
