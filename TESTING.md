# Verifica

## Dimensioni della patch ridotta

- 570 righe aggiunte, 6 rimosse, 12 file coinvolti.
- Un solo file nuovo: modulo di 520 righe, inclusi Unix, Windows e cache.
- Negli 11 file esistenti: 50 righe aggiunte e 6 rimosse.
- 691 righe fisiche nel `.patch`, inclusi intestazioni e contesto.
- SHA-256: `ed15eb37c1c47cc4f9cdb6982d3217b45b0cab8d568c96ed3c48d780db1f8093`.

La versione precedente aggiungeva 1081 righe in 17 file, con cinque file nuovi.
La riduzione elimina i backend separati, le strutture intermedie dei permessi,
il resolver Unix duplicato, le direttive opzionali e la cache negativa.
Non si tratta di codice spostato fuori dalla patch.

Rispetto alla prima versione minima, `auto/unix` non viene più modificato:
il test delle API dei gruppi è accanto alla registrazione del modulo in
`auto/modules`. È rimossa anche la chiamata aggiuntiva nella sonda delle
directory di index. Restano sei chiamate per i file e tre guardie directory/DAV.

## Applicazione e ABI

`tools/package_patch.py` controlla l'assenza di modifiche agli header delle
strutture core/request, a `ngx_module.h`, a `ngx_open_file_cache` e a
`ngx_http_set_disable_symlinks`. Applica lo stesso patch ai commit originali:

- nginx: `ef0aa967dce9d30b824d4c839d3579d2a17e0666`.
- Angie: `417125cb8664863b044c8c56f0f8d6135bc36b6d`.

Il workflow verifica anche l'applicazione ai tarball nginx 1.31.6 e Angie 1.12.2.
I test ABI compilano un modulo dinamico con il module header originale e con
quello patchato e ne verificano il caricamento su entrambi i server Linux.

## Build e confronto dei moduli

Il [workflow CI](https://github.com/mefistofelix/nginx-symlink/actions/workflows/ci.yml)
parte da sorgenti e binari ufficiali con SHA-256 fissato in `tools/releases.json`.
Usa `-V` del binario ufficiale, configura il sorgente originale, applica la
patch e riconfigura. Verifica la tabella dei moduli e le opzioni del binario
compilato: devono restare uguali, con il solo modulo symlink_access aggiuntivo.

Riferimenti: pacchetti ufficiali Ubuntu 24.04 x86_64 (111 moduli nginx e 125
Angie) e zip ufficiale nginx Windows x86. I moduli dinamici separati sono esclusi
da questo confronto. I rapporti `*-modules.json` accompagnano gli eseguibili.
Tutti i job devono riuscire prima della pubblicazione.

## Test automatici

- Unix: 291 controlli HTTP per server, inclusi 250 durante sostituzioni concorrenti
  dei symlink, owner/group/other, gruppi supplementari disponibili, alias, root
  variabili, redirect, index, try_files, gzip, guardie directory/DAV, metadata
  modificati, descriptor e log dinamici. Solo le fixture temporanee cambiano owner.
- Windows: 465 controlli quando la creazione di file symlink è disponibile,
  incluse junction, DACL, file diretti e 250 richieste durante sostituzioni tra
  file diretto e symlink verso un target negato. Senza quel privilegio, il test
  segnala esplicitamente il sottoinsieme eseguito con junction.
- Cache/policy: include il sorgente effettivo del modulo e sostituisce solo NSS.
  Verifica TTL assoluto, collisioni, cache off, lookup fallito e precedenza dei
  permessi con ASan/UBSan. L'instrumentazione ASan delle variabili globali è
  esclusa per eliminare dal link la configurazione HTTP inutilizzata; heap e
  stack dei percorsi testati restano instrumentati.

La prima release `c566eee04a1b` contiene la versione estesa precedente; consultare
le release successive e i relativi run per la patch ridotta.

La patch ridotta ha superato anche 291 verifiche HTTP per server su Alpine 3.23
(musl), e la compilazione forzando l'assenza di `NGX_HAVE_OPENAT`, di
`NGX_HAVE_GETGROUPLIST` e di entrambe le API. Questi controlli locali non
equivalgono a esecuzioni su FreeBSD o macOS.

Non sono validati FreeBSD/macOS, domini Active Directory, share SMB o tutti i
filesystem e reparse provider Windows. Non sono stati effettuati benchmark.
Angie Windows non è distribuito: la build upstream presenta errori indipendenti
dalla patch. Alcune fixture Windows di tentativi precedenti restano nella
directory locale ignorata `.test-windows`, esclusa da repository e release.
