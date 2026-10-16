# RedFear Support

Python 3.11+ / discord.py / SQLite. Bot pentru un singur server: Ajutor, Reclamație, Contestație, Bug; formular, canale private, preluare de staff, închidere cu confirmare, transcriere TXT în arhiva privată. Panoul și butoanele funcționează după restart. Maximum un tichet activ per autor și pauză de 60 secunde între creări. Rulați o singură instanță.

## Configurare Discord

Developer Portal → Bot: Message Content Intent activ; Members și Presence oprite. OAuth2 scopes: bot, applications.commands. Permisiuni: View Channels, Manage Channels, Manage Roles (pentru permisiunile canalelor), Send Messages, Embed Links, Attach Files, Read Message History. Administrator nu este necesar.

Creează categoria privată TICHETE și canalul privat #arhiva-tichete. Permite botului acces și scriere; botul trebuie să poată crea canale și schimba permisiunile. Categoria nu trebuie să acorde Membru acces. Arhiva trebuie accesibilă numai staffului, administratorilor și botului. Codul verifică rolurile obișnuite înainte de export, dar verifică manual excepțiile de acces acordate membrilor individuali.

Activează Developer Mode în Discord și copiază ID-urile în .env: server, categoria TICHETE, arhivă și rolurile Moderator/Helper separate prin virgulă. SUPPORT_ROLE_IDS nu trebuie să includă Membru sau @everyone. Tokenul rămâne doar pe server.

## Linux Ubuntu/Debian + systemd

Clonează repository-ul privat cu autentificarea GitHub proprie și execută din directorul clonat:

```bash
sudo apt update
sudo apt install python3 python3-venv git
sudo useradd --system --user-group --home-dir /opt/redfear-support --shell /usr/sbin/nologin redfear
sudo mkdir -p /opt/redfear-support
sudo cp -r . /opt/redfear-support/
sudo chown -R redfear:redfear /opt/redfear-support
cd /opt/redfear-support
sudo -u redfear python3 -m venv .venv
sudo -u redfear .venv/bin/pip install -r requirements.txt
sudo -u redfear cp .env.example .env
sudo nano .env
sudo chmod 600 .env
sudo -u redfear mkdir -p data
sudo cp deploy/redfear-support.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now redfear-support
sudo journalctl -u redfear-support -n 50 --no-pager
```

Completează tokenul și toate ID-urile pozitive înainte de pornire. Nu publica .env sau data/. Nu sunt necesare porturi de intrare. După conectare, administratorul execută **/ticket_panel** în #ajutor. Panoul respectă accesul canalului: păstrează-l vizibil numai membrilor verificați. Oprește panoul vechi Ticket Tool după validarea noului sistem.

## Test live necesar

Deschide un tichet; verifică accesul autorului/staffului și lipsa accesului altor membri. Al doilea tichet trebuie refuzat. Numai stafful poate prelua. Repornește serviciul și testează butoanele existente. Închide: confirmare, export în arhivă, autor fără acces, canal păstrat pentru staff. Nu se șterg canale automat.

Dacă arhivarea eșuează, tichetul rămâne activ pentru retry; scrierea autorului poate fi blocată deja. Corectează problema și reîncearcă. Exportul parțial poate produce duplicate la retry. Dacă autorul a părăsit serverul, închiderea automată este refuzată; stafful trebuie să arhiveze/închidă manual canalul. Canalele închise se curăță manual înainte de limitele Discord.

Transcrierile includ mesajele încă existente, descrierile embed și URL-urile atașamentelor; nu descarcă fișiere, nu recuperează mesaje șterse sau versiuni anterioare. URL-urile pot expira. Stabiliți retenția și ștergeți manual arhivele vechi. Salvați SQLite cu serviciul oprit sau folosind sqlite backup.

## Teste locale

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot.py store.py
```

Testele locale nu conectează botul la Discord; tokenul și validarea pe server sunt necesare înainte de utilizare reală.
