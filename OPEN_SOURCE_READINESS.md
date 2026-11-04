# Sprintwake Studios before public GitHub — 4 November 2026

**Gjeldende mål og status:** [PROJECT.md](PROJECT.md), med verifikasjon i
[STATUS.md](STATUS.md). Modelltilkoblinger (0.8.0), den felles arbeidsflaten
(0.7.0) og selvstendig app med lokalt testet Sparkle (0.9.0) er levert.
Brukeren autoriserte offentlig kildekode 4. november 2026 og valgte CC BY-NC-SA
4.0. Ren-klone-bygg, native oppstart, lokale CI-kontroller og uavhengig gjennomgang
består. Offentlig signering gjelder en senere Mac-release. Denne filen skiller
inspirasjon fra faktisk støtte.

Denne gjennomgangen gjelder design, arbeidsflyt og filmagentens faktiske
verktøy. Ingen publisering, valg av lisens eller installasjon av nye modeller
inngår. Referanseprosjektenes dokumentasjon er inspirasjonskilder; den dokumenterer
deres tilsiktede funksjoner, ikke verifisert filmkvalitet i Raven.

## Hva vi bør hente

| Prioritet | Inspirasjon og konkret Raven-flyt | Dagens grunnlag og arbeid som gjenstår |
| --- | --- | --- |
| 1 | **Tilkoblinger før produksjon.** Fra Red Clovers samlede tjenesteoppsett og Seriesmiths modellstatus: velg produksjonsmappe, motor, ComfyUI og språkmodell i én innføring. Vis «klar», «mangler», «utilgjengelig» eller «ikke kontrollert», med en konkret utbedring. | Implementert i 0.8.0: Modeller-panelet lagrer private stier, profiler og nøkler og gjør passive kontroller. En ny bruker kan åpne oppsettet uten en fungerende modell. 0.9.0 inkluderer motor og CPU-runtime i appen. Modellstart og vektnedlasting følger ikke av en tilkoblingstest. |
| 2 | **En stabil arbeidsflate med mindre navigasjon.** Fra Seriesmiths klipp-/referanseliste, spiller, tidslinje og inspektør: behold valgt prosjekt/scene/klipp mens brukeren veksler mellom planlegging, produksjon og klipp/lyd. | Raven har allerede disse oppgavene. De store stablede sekvenskortene er erstattet av valg i venstre liste og redigering av valgt gruppe til høyre. Implementert i 0.7.0 (2026-11-04): kompakt sekvens-/sceneliste til venstre, felles spiller i midten, kontekstuelle detaljer til høyre og tidslinje nederst. Scener, Struktur, Regi, Klipp og Lydspor deler arbeidsflate; romkart åpnes i samme prosjektkontekst. |
| 3 | **«Hva mangler, og hvorfor?»** Fra Red Clovers manus-/kortkontroll og Seriesmiths manglende/utdaterte takes: vis årsaken ved hvert berørte klipp og la brukeren gå direkte til rett arbeidsflate. | Kjernen har allerede referansebinding, renderfingeravtrykk, observerte sluttilstander og revisjonsbundne kontrollpunkter. Samle dem i Raven fremfor å lage en konkurrerende kontinuitetsmodell. Skill manglende referanse, endret oppskrift, manglende take og ventende menneskelig vurdering. Ingenting regenereres av en slik diagnose. |
| 4 | **Synlig oppskrift og modellgrense.** Fra OpenFields «Open in ComfyUI» og OpenKatanas kapabilitets-/begrensningstabeller: vis hvilken profil, modell og oppskrift et take faktisk brukte, og hvilke funksjoner profilen støtter. | Implementert i 0.8.0: eksplisitte bilde-/video-profiler deklarerer bindinger og kapabiliteter, og takes beholder oppskrifter. Dette gir ikke vilkårlige workflows støtte for hele filmflyten, videreføring eller alle lyd-/referansefunksjoner. Lokal Qwen/LTX har fortsatt den kvalifiserte filmflyten; se MODEL_CONNECTIONS.md. |
| 5 | **Flytt klippen videre til et vanlig klippeprogram.** Fra Red Clovers Resolve-flyt: eksporter valgt take, kildetrim, rekkefølge og separate lydspor som en redigerbar tidslinje. | Raven eksporterer grovklipp og prosjekt-ZIP. OTIO/FCPXML-rundtur er ikke implementert. Begynn med en definert eksportkontrakt og et lite syntetisk rundturprosjekt; vurder deretter sluttgenerering av bare de klippene som faktisk brukes. |
| 6 | **Bevegelsesstyrt previs.** Fra OpenFields Stage: kamerabane, enkel figurbevegelse, leire-/dybdepass og valgt look-frame. | Raven har et statisk romkart og geometriske layoutbilder. Layout-to-Render/IC-LoRA er ikke kvalifisert i den lokale MLX-motoren. Dette bør være en separat, valgfri adapter med et lite faktisk videoforsøk før funksjonen tilbys. |

Kilder kontrollert 2026-11-03:

- [Seriesmith editor](https://github.com/bgstratt/seriesmith/blob/main/docs/EDITOR.md)
  og [oppsett](https://github.com/bgstratt/seriesmith/blob/main/docs/SETUP.md).
- [Red Clover Studio](https://github.com/ShawnTheWriter/red-clover-studio/blob/main/README.md).
- [OpenField](https://github.com/wexloops/openfield/blob/main/README.md).
- [OpenKatana editor tools](https://github.com/rk0neki/OpenKatana/blob/main/docs/EDITOR_TOOLS.md).

## Filmagenten: avgrenset oppdatering

Problem: Agenten fikk kanoniske shot-kort, filmbibel, bilder og produksjonsstatus,
men så ikke de nye valgene i klippelag, romkart, sekvenser eller lydspor.
Veiledning alene ville derfor gi råd uten faktisk innsikt i brukerens redigering.

Oppdateringen legger til én ren, delt lesemodell for de fire lagrede journalene,
fire validerte `inspect_*`-verktøy og navigasjon til de nye arbeidsflatene.
Konteksten omfatter valgte takes/trim/rekkefølge, figur-/kameraplassering,
grupper/tidsmål og lydsporenes plassering, gain/fades/mute/solo. Historikk og
mediestier inngår ikke. Manglende, ugyldige, ukjente og avkortede dokumenter skal
oppgis eksplisitt. Lesing oppretter eller endrer ingen journaler.

Agenten kan gi konkrete redigeringsråd ut fra dette og åpne riktig arbeidsflate.
Selve klipp-, coverage-, sekvens- og lydredigeringen skjer fortsatt i de native
panelene med deres angrefunksjoner. Ingen nye agentverktøy skriver i disse
journalene. Produksjonsgodkjenning forblir menneskelig; et lest dokument eller
en laget forhåndsvisning beviser ikke at video/lyd er sett og hørt.

Berørte områder: kjernens `raven_editorial.py`, kontrakt, chat-kontekst og
regissørguide; native kontraktbinding, executor og resultatvisning. Verifisering
bruker faktiske journalformater, inert chat og en disponibel native app-kjøring.
Ingen mediegenerering inngår.

## Current portable model connection state

Connection settings now support explicit engine/runtime/workspace paths, the
local Comfy origin, selected image/video profiles and LLM profiles. Defaults
preserve the current local Qwen/LTX installation. The installed 0.9.0 app includes
its engine, pinned Python CPU environment and FFmpeg. `scripts/setup-engine.sh`
remains an optional source-development helper for an explicitly selected external
engine/runtime. Keys use the OS credential loader and are not stored in project data.

The schema-v1 Comfy adapter supports explicit API-exported image/video profiles:
validated bindings and capabilities, passive preflight, independent takes,
immutable recipes and own-job recovery. It does not support arbitrary UI graphs,
native continuation, long-film resume, recorded speech or automatic style
reference translation. Video remains 24 fps with profile-declared limits. See
[MODEL_CONNECTIONS.md](MODEL_CONNECTIONS.md).

## Restarbeid før offentlig førsteversjon

Portabilitet og profiloppsett er implementert; det tidligere utvikleroppsettet
med obligatoriske personlige mapper beskriver ikke lenger den installerte appen.
Egne modeller kobles til gjennom de dokumenterte tjenestene/profilene, ikke ved
å anta at en vilkårlig Comfy-graf kan fylle alle produksjonsroller.

Lisensvalget er CC BY-NC-SA 4.0 med bevart upstream-kreditering; se LICENSE og
NOTICE.md. Oppsettet har bygget fra en ren lokal klone, og 194 native kontroller
består. Alle 268 app-tester, sju CTest-suiter og 341 motortester består; fem
motorkontroller trenger separat ComfyUI-worker-kilde og hoppes over uten den.
Vertsbaserte CI-resultater vises i GitHub Actions. Developer
ID/notarisering, binærens tredjepartsvilkår og separat brukerkvalifisering gjenstår
før en ferdig Mac-app kan publiseres som Release.
Den samlede sjekklisten vedlikeholdes i [PROJECT.md](PROJECT.md).

OTIO/FCPXML-rundtur og bevegelsesstyrt previs er fortsatt forslag, ikke ferdige
funksjoner eller automatisk godkjent videre arbeid. Kildepublisering er nå
autorisert; ferdige Mac-releases krever separat beskjed.

### Konkrete funn i publiseringsgjennomgangen

Motorens `engine/LICENSE` er bevart; appens bidrag bruker samme CC BY-NC-SA 4.0.
`scripts/setup.sh` bruker nå medfølgende `engine/`, med regresjonstest av både
standardstien og eksplisitt ekstern motor. Private oppsett og deres midlertidige
skrivefiler er Git-unntatt. CI-testenes gamle utviklermappestier er rettet uten å
hoppe over de rene motor-/kontraktkontrollene. Gjeldende sjekkliste står i
PROJECT.md; Developer ID/notarisering gjelder den senere Mac-releasen.
