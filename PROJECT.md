# Sprintwake Studios — mål og prosjektstatus

Oppdatert 5. november 2026. Installert og ny kildeversjon: **0.11.3, build 20 (MIT)**.
Lokal oppdatering er utført uten tester eller generering, etter uttrykkelig beskjed.
Oppstart kan kreve brukerens godkjenning av eksisterende Nøkkelring-tilgang.
Installasjonsutfallet føres i STATUS.md. Endringene beskrives på
engelsk i [CHANGELOG.md](CHANGELOG.md).
Dette er den samlede oversikten over brukerens mål, levert arbeid og neste steg.
[STATUS.md](STATUS.md) inneholder detaljert verifikasjon og historikk.

## Målet

Bygge et selvstendig, lokalt filmstudio for både korte og lange filmer: fra idé
og manus til filmbibel, rom-/kameraplan, produksjon, redigering, lyd og eksport.
Brukeren skal kunne bearbeide filmen og holde kontinuiteten uten å generere
medier på nytt for hver redigeringsendring.

Appen og det planlagte åpne prosjektet heter **Sprintwake Studios**. Ett GitHub-repo
skal romme app, produksjonsmotor, bidrag og fremtidige Releases. Andre skal kunne
bruke egne støttede modelltilkoblinger; dagens lokale Qwen/LTX-oppsett beholdes
som standard hos brukeren. Brukeren autoriserte kildekodepublisering 4. november
2026 og valgte først **CC BY-NC-SA 4.0**. Den 5. november ba brukeren om å erstatte CC-koden og valgte **MIT** for egen, uavhengig implementert kode. Den nye motoren erstatter hele den tidligere serveren, kopierte kontrakter, veiledning og webklient; historiske versjoner beholder lisensen sin. Kildekoden er nå offentlig
i [GitHub-repoet](https://github.com/oddmundleikvoll/sprintwake-studios). Ferdige Mac-releases
krever separat publiseringsbeskjed og distribusjonskvalifisering.

## Det som er gjort

- **Bedre filmregi i 0.11.2, tester utsatt:** tydelige roller for valgte
  bildehenvisninger; figurer, steder, rekvisitter og planlagte tilstander følger
  native bilde-/videoprompter. Manusbatcher får nylig kontinuitetskontekst og
  konkrete krav til før-/ettertilstand. Gjennomgangen sammenligner faktisk hale
  med neste planlagte åpning og lagrer menneskelige observasjoner med klipphash.
  Brukeren bestilte deretter lokal appoppdatering og GitHub-publisering med
  engelsk changelog, fortsatt uten tester eller generering. Se STATUS.md.

- **Uavhengig MIT-motor:** nytt lager/HTTP-API, regissørkontrakt, providere, filmjournal og oppdateringslås. Lokal LTX kjører gjennom MIT CLI; Qwen bruker native Comfy og en egen synkron minnebarriere. Skyomtalen står før første README-skjermbilde. Teknisk CPU-/API- og vindusverifikasjon bruker syntetiske medier; ingen ny modellfilm er generert.

- **Ny startside:** tre kort for bilde, enkeltklipp og Filmstudio. Appen åpner
  på Start selv om den forrige samtalen tilhører et prosjekt. Egne, enkle
  innganger til bilder og klipp; lagrede arbeider åpnes i riktig studio.
  Åpne-knappene virker også uten utfylt navn eller beskrivelse; fremdrift og
  eventuelle feil vises ved knappen.
- **Klipp og scener:** separat klippelag med takes, trim, rekkefølge og angre uten
  regenerering eller endring av originalmediene.
- **Romkart og coverage:** plassering av figurer og kameraer, geometrisk
  forhåndsvisning og forslag til kameravinkler med kontinuitetskontroller.
- **Sekvenser og lyd:** scene-/sekvensstyring og spilletidsmål; egne spor for
  dialog, musikk, miljølyd og effekter som kan gå over klippgrenser.
- **Arbeidsflate:** sceneliste til venstre, spiller i midten, detaljer til høyre
  og tidslinje nederst. Eksisterende farger og typografi er beholdt.
- **Filmagent:** oppdatert veiledning og innsyn i lagrede klipp-, romkart-,
  struktur- og lydbeslutninger. Panelene eier fortsatt disse redigeringene;
  lesing eller agenttekst teller ikke som menneskelig produksjonsgodkjenning.
- **Modelltilkoblinger:** privat oppsett, nøkkelringlagring av nøkler, passive
  kontroller og eksplisitte bilde-/video-profiler for Comfy API-workflows.
- **Selvstendig app:** motor, Python, CPU-avhengigheter og medieverktøy følger
  appen. Den installerte appen trenger ikke utviklerens kildekodemappe.
  Modellvekter og eksterne modelltjenester ligger fortsatt utenfor appen.
- **Oppdatering:** Sparkle og ett GitHub Releases-oppsett er klargjort. En ekte,
  lokalt signert oppdatering ventet på produksjonen, installerte, startet på nytt
  og bevarte kontrollfilene. En manipulert signert feed ble avvist.
- **Installering og navn:** Sprintwake Studios er installert i
  `~/Applications/Sprintwake Studios.app`, med Sprintwake-logoen i kobber/gull på
  mørk plommebakgrunn. Prosjekter og lokalt modelloppsett er bevart.
- **Opprydding:** fem gamle apparkiver er flyttet til papirkurven, og 49
  gjenstående appregistreringer fra papirkurven er fjernet. Bare den installerte
  MIT-appen er igjen utenfor papirkurven og registrert som Sprintwake Studios.
  Avregistrering må skje på den faktiske papirkurvstien etter flytting.
  Sprintwake Projects er en annen app og er bevart.

## Verifisert — og hva testene ikke beviser

265 app-tester, sju Raven CTest-suiter og 194 vinduskontroller fra både kilde og
appakken bestod for 0.10.1. Begge åpne-knappene ble deretter klikket med tomme
felt i den installerte appen, med egne testmapper. Vanlig oppstart er kontrollert:
Start vises, og den eksisterende filmen ligger under nylige arbeider.
Elleve eksisterende prosjekt-/oppsettfiler var byte-identiske etter installasjon
og oppstart. Testkopiene ble avregistrert og flyttet til papirkurven.

For 0.9.0 bestod også 332 motortester (to hoppet over) og kontrollene for signerte
oppdateringer, omstart, aktive leserlåser og rollback, inkludert en flyttet,
skrivebeskyttet appkopi. Motor- og oppdateringskoden er beholdt i 0.10.0.

Dette er teknisk kvalifisering med syntetiske medier og testtjenester, ikke bevis
på kvaliteten til en ferdig langfilm eller støtte for alle eksterne modeller.
Automatisk filmproduksjon og videreføring er fortsatt kvalifisert for lokal
Qwen/LTX. Vilkårlige Comfy-grafer får ikke automatisk de samme egenskapene.

## Engelsk GitHub-versjon 0.10.2

Appens grensesnitt, menyer og nye standardtekster er engelske. AI-regissøren bruker
engelsk som standard og følger brukerens språkvalg. Eksisterende prosjektinnhold
og lagrede navn beholdes. GitHub-beskrivelsen og README fremhever lange filmer,
innebygd AI-regissør og lokal kjøring, med ekte skjermbilder fra et fiktivt
prosjekt. Regissørsamtalen er tydelig merket som en skriptet demo.

269 app-tester, sju CTest-suiter, 342 CPU-kontroller (fem valgfrie worker-hopp) og
194 vinduskontroller fra både kilde og flyttet, skrivebeskyttet app består.
Modellinferens ble ikke startet. Installert app, prosjekter og modelloppsett er
urørt. Den offentlige kildekoden kan bygges lokalt uten Developer ID/notarisering.
[GitHub CI for 0.10.2 består](https://github.com/oddmundleikvoll/sprintwake-studios/actions/runs/41924455474).

## Forebygging av appkopier

Fremtidige bygg bruker `.noindex`, og vellykket installasjon forbruker byggekilden
og rydder gammel app. Disponibel byggkontroll og Sparkle-prøver rydder egne kopier
også ved feil. Gamle appmapper blir kontrollerte ZIP-sikkerhetskopier i papirkurven,
siden macOS ellers kan oppdage dem igjen. Opprydding beholdes ved installasjon og
én gang etter bekreftet oppdatering. Vanlig oppstart og ny backend-tilkobling
starter ingen opprydding. Brukeren bestilte også lokal installasjon og GitHub
med engelsk changelog for 0.11.3/build 20, uten tester eller generering.

## MIT-erstatning (0.11)

Den uavhengige motoren er verifisert med 80 CPU-/API-kontroller uten hopp,
269 app-tester og sju CTest-suiter. Det faktiske vinduet består 194 kontroller
både i kildekopien og i en flyttet, skrivebeskyttet app. En lokal Sparkle-prøve
oppdaterte testkopien fra build 17 til 18 og bevarte åtte filer; en manipulert
signert feed ble avvist mens produksjonen fortsatte. Ingen modellinferens ble
startet. Detaljer og begrensninger står i [STATUS.md](STATUS.md).

Den installerte MIT-appen er signaturkontrollert og åpner med den nye motoren.
Et ferskt, flyttet appbygg bestod 194 vinduskontroller før installasjon.
Alle 151 eksisterende kontrollerte produksjons-/modellfiler er bevart.
Samme lokale regissørmodell bruker nå oMLX direkte med endpoint-bundet nøkkel
i Nøkkelring; private originalinnstillinger er sikkerhetskopiert. Eksisterende
LTX-runtime er koblet inn, og den nye Comfy-hjelpenoden er installert.
ComfyUI og modellgenerering er ikke startet som del av oppdateringen.

## Offentlig GitHub-kilde

### Historisk første kildeversjon (0.10.x)

1. **Lisens valgt:** [LICENSE](LICENSE) bruker CC BY-NC-SA 4.0 for appens bidrag,
   med bevart [engine/LICENSE](engine/LICENSE). [NOTICE.md](NOTICE.md) krediterer
   Storyboard/Peter Chodyra og beskriver endringene og øvrige avhengigheter.
2. **Ren klone:** [scripts/setup.sh](scripts/setup.sh) bruker medfølgende
   `engine/`. En separat lokal Git-klone uten naboinstallasjonen har bygget den
   komplette appen og bestått 194 native vinduskontroller uten modellinferens.
   Alle 268 app-tester og sju CTest-suiter består uten hopp. Motorens 342
   CPU-kontroller består med fem valgfrie hopp for separat ComfyUI-worker-kilde.
   Rene motor-/kontraktkontroller kjøres fra repoets egne filer.
3. **Publiseringsinnhold:** private motoroppsett, sikkerhetskopier, midlertidige
   JSON-filer, nøkler, låser og binærverktøy er utelatt fra Git. Den første
   kildegjennomgangen fant ingen nøkkelmønstertreff, store filer eller symlenker.
   Private kvalifiseringstitler og hjemmemappestier er anonymisert.
   [CONTRIBUTING.md](CONTRIBUTING.md) dokumenterer utviklerkontrollene.
4. **Publisert og verifisert:** første kildeversjon bruker det
   eksisterende [GitHub-repoet](https://github.com/oddmundleikvoll/sprintwake-studios).
   Ingen appbinær, appcast eller modellvekter inngår. Uavhengig gjennomgang er
   ferdig, og [full GitHub CI består](https://github.com/oddmundleikvoll/sprintwake-studios/actions/runs/41914411964).

### Før en ferdig Mac-app legges ut som Release

Skaffe Developer ID-signering, fullføre notarisering og kvalifisere den offentlig
signerte pakken/oppdateringen. Dagens app er lokalt ad-hoc-signert. Dette er et
separat distribusjonsløp; Apple-signering er ikke et krav for å åpne kildekoden.

Gjeldende kodelisens: [MIT](LICENSE). Historiske CC-versjoner beholder sine vilkår.
Appdistribusjon: [Apple Developer ID](https://developer.apple.com/developer-id/).

OTIO/FCPXML-rundtur til andre klippeprogrammer og bevegelsesstyrt previs er mulige
senere forbedringer. De er ikke implementert eller bestilt som neste arbeid.

## Hvilken fil svarer på hva?

- [README.md](README.md): bruk og oppstart.
- [STATUS.md](STATUS.md): levert arbeid, testresultater og historikk.
- [CONTEXT.md](CONTEXT.md): kodeområder og tekniske grenser.
- [MODEL_CONNECTIONS.md](MODEL_CONNECTIONS.md): modellprofiler og begrensninger.
- [DISTRIBUTION.md](DISTRIBUTION.md): bygging, installasjon og oppdatering.
- [OPEN_SOURCE_READINESS.md](OPEN_SOURCE_READINESS.md): inspirasjon og offentlig omfang.
- [AGENTS.md](AGENTS.md): arbeidsregler, kommandoer og bevaringskrav.

Eldre `*_PLAN.md` og `IMPLEMENTATION.md` er implementeringshistorikk, ikke en
oppdatert oppgaveliste. Ved motstrid brukes denne oversikten og siste STATUS.
