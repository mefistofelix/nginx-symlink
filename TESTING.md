# Verifica della patch

Patch unica: `patches/symlink-access.patch`.

- SHA-256: `761794b576bb796b04a31da70fed7f56083cac78fd47df843a3398e523197548`.
- 44.914 byte; 1.247 righe nel file patch, inclusi contesto e intestazioni.
- 1.081 righe aggiunte e 9 rimosse: 12 file esistenti modificati e 5 nuovi.
- Nei file esistenti: 63 righe aggiunte e 9 rimosse.
- Nei file nuovi: 1.018 righe, di cui 374 nel backend Windows.
- Documentazione, test e strumenti di verifica sono esterni alla patch.

Nessuna modifica alle strutture esistenti, alla firma ABI, al codice di
`ngx_open_cached_file`, a `ngx_http_set_disable_symlinks` o ai relativi header
core. Il confezionamento della patch controlla espressamente questi file.

## Applicazione e build

`tools/package_patch.py` ha estratto entrambi i commit originali in directory
temporanee, applicato esattamente lo stesso file patch e confrontato il risultato
con i sorgenti effettivamente compilati:

| Server | Commit | Risultato |
| --- | --- | --- |
| nginx | `ef0aa967dce9d30b824d4c839d3579d2a17e0666` | Applicazione esatta, verificata |
| Angie | `417125cb8664863b044c8c56f0f8d6135bc36b6d` | Applicazione esatta, verificata |

| Ambiente | nginx | Angie |
| --- | --- | --- |
| Ubuntu, GCC 13, glibc, WSL2 | Build riuscita; 291 verifiche HTTP | Build riuscita; 291 verifiche HTTP |
| Alpine 3.23, musl, WSL2 | Build riuscita; 291 verifiche HTTP | Build riuscita; 291 verifiche HTTP |
| Windows nativo, NTFS; cross-build MinGW-w64 GCC 13 | Build riuscita; 215 verifiche HTTP native | Oggetti della patch compilati; eseguibile completo non verificabile per errori upstream |

Le 291 verifiche Unix per esecuzione includono 250 richieste durante
retargeting concorrente del symlink. I casi di gruppi supplementari vengono
eseguiti quando l'account locale selezionato ne possiede. La verifica delle
chiusure dei descriptor usa `/proc` negli ambienti Linux utilizzati.

La suite Windows ha verificato sia junction di directory sia symlink di file
sulla macchina corrente, oltre a owner, gruppi, Everyone, DACL vuota, NULL DACL,
ACE deny, ACE inherit-only e cambi di ACL con open_file_cache già attiva.
Le ACE deny di scrittura usate nella fixture lasciano il file nativamente
leggibile: il controllo mirato nega comunque la DACL non rappresentabile.

## Cache e ABI

`tests/run_cache_policy.sh` compila il codice comune effettivo con backend
deterministico e AddressSanitizer/UndefinedBehaviorSanitizer. Verificati:

- Scadenza assoluta dei gruppi, senza rinnovo a ogni hit.
- Scadenza separata dei lookup negativi.
- Espulsione per collisione e confronto dell'identità completa.
- Cache disabilitata e nessun riuso di lookup falliti come lista vuota valida.
- Precedenza owner/group/other e diniego dei permessi non utilizzabili.
- Rilascio della memoria della cache.

Nessun errore ASan/UBSan nella suite eseguita.

`tests/module_abi.py` ha verificato, su entrambi i server Linux, il caricamento
di un modulo dinamico di prova compilato con il module header originale e con
quello del checkout patchato. Entrambi sono accettati. Le normali condizioni
di compatibilità della specifica build nginx/Angie continuano ad applicarsi.

## Limiti verificati

La build Windows di Angie fallisce anche estraendo e compilando il commit
originale senza patch. Gli errori includono riferimenti non disponibili a
`ngx_init_setproctitle` e `ngx_update_process_title`, oltre a problemi in
`ngx_log.c`. La patch non contiene una riscrittura del port Windows di Angie.

FreeBSD e macOS non sono stati eseguiti o compilati in questa sessione.
Non sono stati testati domini Active Directory, share SMB, tutti i tipi di
reparse point, tutti i moduli di terze parti o tutte le release dei due server.
Non sono stati effettuati benchmark di throughput o latenza.

Due directory temporanee di precedenti tentativi della suite Windows sono
rimaste in `.test-windows`: la pulizia è stata bloccata dal controllo automatico
degli strumenti. Sono escluse dalla patch e non contengono sorgenti distribuiti.
