# Időkép Weather & Forecast Home Assistant integráció

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ztarsoly/ha-idokep-weather-and-forecast)](https://github.com/ztarsoly/ha-idokep-weather-and-forecast/releases)

Egyéni (custom) Home Assistant integráció, amely az [idokep.hu](https://www.idokep.hu) adatait hozza be: aktuális időjárást, óránkénti és napi előrejelzést, időjárási riasztásokat, egy vagy több választható időjárás-állomás (automata) mért adatait és az országos radarképet.

Az integráció az idokep.hu nyilvános oldalait olvassa (nincs hivatalos API, felhasználói fiók sem kell). Ha az idokep.hu megváltoztatja az oldalait, az integrációt is frissíteni kell.

## Funkciók

- **Időjárás entitás** óránkénti és napi (kb. 23 napos) előrejelzéssel, a Home Assistant új forecast API-jával
- **Előrejelzési szenzorok**: hőmérséklet, állapot, mai maximum/minimum, szélirány, szélerősség, becsült szélsebesség és széllökés, csapadék, csapadék valószínűsége, valamint a település állomásainak átlagából páratartalom, harmatpont, légnyomás és UV-index, és ezekből számított hőérzet
- **Riasztások**: riasztás 3 órán belül, figyelmeztetés mára (nincs, sárga, narancs, piros; típusonként is) és vihar egy órán belül
- **Időjárás-állomások** (nem kötelező): egy vagy több idokep.hu automata 12 mért adata egy eszközben összevonva, belőlük számított értékek (harmatpont, hőérzet, nedves hőmérséklet, abszolút páratartalom, páranyomás, páranyomás-hiány, felhőalap) és a mérés ideje
- **Országos radar**: animált GIF az utolsó 6 radarképből, külön szolgáltatásként
- Magyar és angol fordítás, a Home Assistant nyelvének megfelelően
- Csak a szükséges oldalakat tölti le, és a legutóbbi jó adatokat lemezre menti, így újraindítás után is azonnal van adat

## Követelmények

- Home Assistant 2026.7 vagy újabb
- Internetkapcsolat a `www.idokep.hu` eléréséhez

A szükséges Python-csomagot (beautifulsoup4) a Home Assistant telepíti.

## Eszközök és entitások

Minden felvett bejegyzéshez (településhez) az alábbiak jönnek létre. A `<Név>` a beállításkor megadott név. Az eszközök neve a Home Assistant nyelvét követi (angolul `<Név> Forecast`, `<Név> Weather`).

### `<Név> Előrejelzés` – Weather forecast

| Entitás | Típus | Megjegyzés |
|---|---|---|
| `<Név> Előrejelzés` | időjárás | aktuális állapot, hőmérséklet, szél, becsült széllökés (a becsült szélsebesség 1,75-szerese), páratartalom, harmatpont, hőérzet, légnyomás, UV-index, becsült felhőzet (%), az aktuális óra csapadéka (`precipitation` attribútum, mm); óránkénti és napi előrejelzés (felhőzettel) |
| Hőmérséklet | szenzor (°C) | aktuális |
| Állapot | szenzor (felsorolás) | pl. napos, derült éjszaka (napnyugta után), borult, eső |
| Felhőzet | szenzor (%) | **becslés** az aktuális állapot leírásából (0, 25, 50, 75 vagy 100%; lásd lent) |
| Mai maximum hőmérséklet | szenzor (°C) | a napi előrejelzésből, éjfélig megmarad |
| Mai minimum hőmérséklet | szenzor (°C) | a napi előrejelzésből, éjfélig megmarad |
| Szélirány (fok) | szenzor (°) | az aktuális óra előrejelzéséből |
| Szélirány (égtáj) | szenzor (felsorolás) | 16 égtáj (É, ÉÉK, ÉK, …) |
| Szélerősség | szenzor (felsorolás) | Beaufort-skála 0–12 (Szélcsend … Orkán) |
| Szélsebesség | szenzor (km/h) | **becslés**: a szélerősség Beaufort-fokozatának alsó határa (az idokep.hu nem ad számot) |
| Széllökés | szenzor (km/h) | **becslés**: a becsült szélsebesség 1,75-szerese |
| Csapadék | szenzor (mm) | az aktuális óra csapadéka az előrejelzésből (0,1 mm pontossággal) |
| Csapadék valószínűsége | szenzor (%) | az aktuális óra előrejelzéséből; 0, ha az oldal nem mutat értéket |
| Páratartalom | szenzor (%) | a település állomásainak átlaga a páratartalom-térképről (`/para`) |
| Harmatpont | szenzor (°C) | a település állomásainak átlaga a harmatpont-térképről (`/para`) |
| Hőérzet | szenzor (°C) | számított a hőmérsékletből, a páratartalomból és a **becsült** szélsebességből (Steadman-képlet, napsugárzás nélkül), minden frissítéskor |
| Légnyomás | szenzor (hPa) | a település állomásainak átlaga a légnyomástérképről (`/legnyomas`), tengerszintre átszámítva |
| UV-B index | szenzor | a település állomásainak átlaga az UV-térképről (`/uv`); kevés állomás méri |
| Riasztás 3 órán belül | szenzor (felsorolás) | a legmagasabb szint az óránkénti kártyák riasztás-ikonjaiból: Nincs riasztás, Sárga, Narancs vagy Piros riasztás; az idokep.hu ezeket az aktuális és a következő két órához teszi ki. Az `alerts` attribútum az érvényes riasztásokat sorolja fel |
| Figyelmeztetés mára | szenzor (felsorolás) | az oldal tetején lévő sáv(ok) legmagasabb szintje, amely riasztás vagy figyelmeztetés is lehet: Nincs figyelmeztetés, Sárga, Narancs vagy Piros figyelmeztetés. A `warnings` attribútum az érvényes figyelmeztetéseket sorolja fel |
| Vihar várható egy órán belül | bináris szenzor | bekapcsol, ha az aktuális időjárás viharos (10 percenként frissül), vagy az előrejelzésben az aktuális vagy a következő óra viharos (óránként frissül). Viharos: zivatar, száraz zivatar, villámlás, jégeső, eső viharos széllel, porvihar |

Az `alerts` és a `warnings` attribútum csak az érvényben lévő típusokat tartalmazza, típusonként a legmagasabb szinttel. Elöl a legmagasabb szint áll, az azonos szintűek a lenti típussorrendben követik egymást, például:

```yaml
alerts:
  - label: Szél
    type: wind
    level: yellow
```

- `label`: a típus neve a Home Assistant nyelvén
- `type`: az idokep.hu saját típusneve angolul: `thunderstorm` (zivatar), `wind` (szél), `rain` (eső), `freezing_rain` (ónos eső), `blowing_snow` (hófúvás), `snow` (havazás), `fog` (köd), `frost` (fagy), `heat` (hőség); `other`, ha a szövegből nem derül ki
- `level`: `yellow`, `orange` vagy `red`

A páratartalom, a harmatpont, a hőérzet, a légnyomás és az UV-B index „nem elérhető”, ha a településnek nincs ilyen értéket mérő állomása.

A Felhőzet szenzor és az időjárás entitás felhőzete (`cloud_coverage`) **becslés** az idokep.hu állapotleírásából, mert az oldal nem ad számszerű értéket. A felhőszavak a nyolcadokat követik: derült és napos 0%, gyengén felhős 25%, közepesen felhős 50%, erősen felhős 75%, borult 100%. A tartós eső, hó, ónos eső és köd 100%, a zápor, hózápor, zivatar, villámlás és jégeső 75%, a felhőzetről semmit nem mondó leírások (párás, pára, szeles, hófúvás, porvihar) 50%. Ismeretlen leírásnál nincs érték.

### `<Név> Időjárás` – Current weather (csak állomáskóddal)

Egy vagy több idokep.hu időjárás-állomás (automata) mért adatai, egy eszközben. Több állomásnál minden érték a választott **összevonás módja** szerint áll össze:

- **Prioritás**: az első olyan állomás értéke a listában, amelynek van elég friss mérése; a következő állomás pótolja, amit az előző nem mér, vagy ha épp nem érhető el.
- **Legfrissebb**: annak az állomásnak az értéke, amelynek a legutolsó az elég friss mérése (egyenlőségnél a listában előbbié).
- **Átlag**: a friss mérések átlaga (a szélirány szögként átlagolva).

Minden szenzor `stations` attribútuma mutatja, melyik állomás(ok)tól származik az érték. Azok az értékek, amelyeket egyik állomás sem mér, „nem elérhető” állapotúak (a számított értékek akkor, ha valamelyik bemenetüket nem méri egyik állomás sem); ha egyiknek sincs a **Mérés maximális kora** beállításnál (alapból 60 perc) frissebb mérése, „ismeretlen”. Ha egy állomás épp nem érhető el, a többi adatai jelennek meg.

| Entitás | Mértékegység | Megjegyzés |
|---|---|---|
| Hőmérséklet | °C | mért |
| Relatív páratartalom | % | mért |
| Légnyomás | hPa | mért |
| 24 órás csapadék | mm | mért |
| Éjfél óta hullott csapadék | mm | mért |
| Csapadékintenzitás | mm/h | mért |
| Szélsebesség | km/h | mért |
| Széllökés | km/h | mért |
| Szélirány | ° | mért |
| UV-B index | – | mért |
| Globálsugárzás | W/m² | mért |
| Vízhőmérséklet | °C | mért |
| Harmatpont | °C | számított (Magnus-képlet) a hőmérsékletből és a relatív páratartalomból |
| Hőérzet | °C | számított (Steadman-féle látszólagos hőmérséklet, napsugárzás nélkül) a hőmérsékletből, a relatív páratartalomból és a szélsebességből |
| Nedves hőmérséklet | °C | számított (Stull-képlet) a hőmérsékletből és a relatív páratartalomból |
| Abszolút páratartalom | g/m³ | számított: egy köbméter levegő víztartalma |
| Páranyomás | hPa | számított: a levegő nyomásából a vízgőzre jutó rész (a levegő víztartalmának mértéke) |
| Páranyomás-hiány | kPa | számított: mennyire van távol a levegő a telítettségtől |
| Felhőalap | m | számított **becslés** az állomás fölött (125 m a hőmérséklet és a harmatpont minden °C különbségére); gomolyfelhőkre érvényes |
| Mérés ideje | időbélyeg | a legfrissebb felhasznált mérés ideje |

### `Országos Radarkép` (National Radar Image) – Radar szolgáltatás

Az első bejegyzés felvételekor automatikusan létrejön egy **Radar** bejegyzés egy kép entitással (**Országos radarkép**, angolul National radar image). Ez az országos radar utolsó 6 képéből készült animált GIF (képenként 1 mp, az utolsón 2 mp). Ha törlöd, a következő újraindításkor vagy újratöltéskor újra létrejön; ha nem kell, inkább kapcsold ki.

## Telepítés

### HACS-on keresztül

1. Nyisd meg a **HACS**-t, kattints a jobb felső **⋮** gombra, és válaszd az **Egyéni tárolók** (Custom repositories) menüpontot.
2. Add meg a tároló címét: `https://github.com/ztarsoly/ha-idokep-weather-and-forecast`, típusnak válaszd az **Integráció** (Integration) lehetőséget.
3. Keresd meg az **Időkép Weather & Forecast** integrációt, és telepítsd.
4. Indítsd újra a Home Assistantot.

### Kézzel

1. Töltsd le az `idokep_weather_and_forecast-<verzió>.zip` fájlt a [legutóbbi kiadásból](https://github.com/ztarsoly/ha-idokep-weather-and-forecast/releases/latest).
2. Csomagold ki a Home Assistant konfigurációs mappájában (ahol a `configuration.yaml` van) lévő `custom_components` mappába; ha nincs ilyen mappa, hozd létre. A végén a `custom_components/idokep_weather_and_forecast/manifest.json` fájlnak kell meglennie.
3. Indítsd újra a Home Assistantot.

Frissítéshez töröld a `custom_components/idokep_weather_and_forecast` mappát, csomagold ki ugyanígy az új kiadás zip-fájlját, és indítsd újra a Home Assistantot.

## Beállítás

**Beállítások → Eszközök és szolgáltatások → Integráció hozzáadása → Időkép Weather & Forecast**

| Mező | Kötelező | Leírás |
|---|---|---|
| Név | igen | A bejegyzés és az eszközök neve (pl. Otthon → „Otthon Előrejelzés”, „Otthon Időjárás”) |
| Település | igen | A település neve úgy, ahogy az idokep.hu címében szerepel, pl. `Budapest` vagy `Debrecen-Józsa` (`https://www.idokep.hu/idojaras/Debrecen-Józsa`) |
| Időjárás-állomások kódjai | nem | Egy vagy több idokep.hu automata kódja fontossági sorrendben, pl. `jozsamet` (`https://www.idokep.hu/automata/jozsamet`) |
| Összevonás módja | nem | Prioritás (alapértelmezett), Legfrissebb vagy Átlag – lásd fent |
| Mérés maximális kora | nem | Az ennél régebbi állomásmérés nem számít, percben (1–1440, alapból 60) |

Az állomáskódot az [idokep.hu/automata](https://www.idokep.hu/automata) oldalon találod: válaszd ki az állomást a listából, a kód a címben a `/automata/` utáni rész (a listában zárójelben is látszik).

Egy település csak egyszer vehető fel. Több települést külön bejegyzésként adhatsz hozzá.

### Módosítás később

A bejegyzés **Beállítás** (Configure) gombjával módosítható:

- **Település**: más településre vált; a bejegyzés és az eszközök neve, valamint az entitások azonosítói megmaradnak.
- **Időjárás-állomások kódjai**: állomások hozzáadása, törlése vagy sorrendjük módosítása; mind törölve a `<Név> Időjárás` eszköz megszűnik.
- **Összevonás módja**: Prioritás, Legfrissebb vagy Átlag.
- **Mérés maximális kora**: az ennél régebbi állomásmérés nem számít, percben.

A bejegyzés nevét a Home Assistant saját **Átnevezés** funkciójával lehet módosítani.

## Frissítési ütemezés

| Adatforrás | Mit frissít | Ütemezés | Hiba esetén |
|---|---|---|---|
| Aktuális időjárás (`/idojaras/`) | hőmérséklet, állapot | 10 percenként (x:00:59, x:10:59, x:20:59, …) | percenként újra |
| Előrejelzés (`/elorejelzes/`) | óránkénti és napi előrejelzés, mai max/min, szél, riasztások | óránként (x:00:59) | 10 percenként újra |
| Időjárás-állomások (`/automata/<kód>`) | állomás szenzorai | 5 percenként (x:00:59, x:05:59, …), minden állomás egyszerre | percenként újra, amíg valamelyik nem érhető el |
| Páratartalom-térkép (`/para`) | páratartalom, harmatpont | 10 percenként (x:00:59, x:10:59, x:20:59, …) | percenként újra |
| Légnyomástérkép (`/legnyomas`) | légnyomás | 10 percenként (x:00:59, x:10:59, x:20:59, …) | percenként újra |
| UV-térkép (`/uv`) | UV-B index | 10 percenként (x:00:59, x:10:59, x:20:59, …) | percenként újra |
| Radar (`/radar/`) | radarkép | 5 percenként (x:00:59, x:05:59, …) | percenként, amíg új kép nem érkezik |

- Indításkor (újraindítás, újratöltés, új bejegyzés) minden adat azonnal frissül.
- Csak az az oldal töltődik le, amelyet legalább egy bekapcsolt entitás használ; a többi újraindításkor sem. A térképoldalakhoz a saját szenzoraik tartoznak: ha egy oldal minden szenzorát kikapcsolod, az oldal letöltése leáll, és az időjárás entitás megfelelő attribútumai is eltűnnek. Az előrejelzési oldalt az időjárás entitás is használja, így az csak akkor áll le, ha az is ki van kapcsolva.
- Ha a bejegyzés rendszerbeállításaiban kikapcsolod a lekérdezést (**Enable polling for updates**), az ütemezett frissítés leáll.

## Adattárolás

Az előrejelzési oldalak és az állomástérképek legutóbbi sikeres adatai a Home Assistant `config/.storage/idokep_weather_and_forecast.<bejegyzés azonosító>` fájljába kerülnek. Újraindítás után visszatöltődnek:

- el nem múlt órák és napok, a mai max/min aznap;
- a riasztások és figyelmeztetések akármilyen régiek is;
- az aktuális szél, csapadék és zivatar a letöltés órájában, utána az előrejelzés aktuális órájából;
- a többi adat (aktuális időjárás, páratartalom, harmatpont, légnyomás, UV-index) akármilyen régi is, amíg egy sikeres letöltés le nem cseréli.

Ugyanezek a szabályok érvényesek futás közben is, amíg az előrejelzés nem tölthető le: óraváltáskor az aktuális szél, csapadék és zivatar a megtartott előrejelzés következő órájából jön, az elmúlt órák kikerülnek az előrejelzésből, a riasztások és figyelmeztetések pedig megmaradnak. Egy sikeres letöltés ezeket azonnal felülírja.

Így az entitások akkor is kapnak értéket, ha az idokep.hu épp nem érhető el (például egy áramszünet utáni újraindításkor, amíg az internet még nem működik). A bejegyzés törlésekor a fájl is törlődik.

Az időjárás-állomások mérései és a radar adatai nem kerülnek lemezre. Futás közben, ha egy állomás nem érhető el, a szenzorai a legutóbbi méréseit mutatják (a **Mérés maximális kora** beállításnál régebbi mérés itt sem számít); újraindítás után az első sikeres letöltésig nem elérhetők.

## Megjegyzések

- A szélsebesség a Beaufort-fokozat alsó, a széllökés ennek 1,75-szerese, tehát mindkettő **becslés**; a valódi széllökés ennél nagyobb is lehet. Mért értékeket az időjárás-állomás ad.
- Este, amikor az idokep.hu már nem mutatja a mai napot, a mai napi előrejelzés és a mai max/min éjfélig megmarad.
- Az idokep.hu az óránkénti előrejelzést a következő órától listázza; az aktuális óra egy korábbi lekérdezésből megmarad, így az előrejelzés az aktuális órával kezdődik (közvetlenül az első telepítés után még a következő órával).
- Az állomásadatok az idokep.hu grafikonjai mögötti adatfájlból származnak, ami nem hivatalos felület, és megváltozhat.

## Köszönetnyilvánítás

Ez az integráció [FabianGabor](https://github.com/FabianGabor) [HA-Idokep](https://github.com/FabianGabor/HA-Idokep) integrációjára épül. Köszönet az eredeti munkáért!

Az időjárási adatok forrása az [Időkép](https://www.idokep.hu). Ez a projekt nem az Időkép hivatalos terméke.

## Licenc

[MIT](LICENSE)
