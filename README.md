# symlink_access per nginx e Angie

Una patch comune aggiunge un controllo mirato alla lettura dei file raggiunti
tramite symlink, usando come identità il proprietario della webroot corrente.
I dati Unix e Windows passano attraverso la stessa interfaccia ridotta:
identità e gruppi dell'utente, owner e bit di accesso del target.

La patch è in **[patches/symlink-access.patch](patches/symlink-access.patch)**.
Richiede la ricompilazione del server. Non cambia strutture esistenti o firma ABI.
Non è un modulo caricabile su binari preesistenti.

## Build e release

Il workflow manuale [ci](.github/workflows/ci.yml) compila e testa nginx 1.31.6
su Ubuntu 24.04 x86_64 e Windows x86, e Angie 1.12.2 su Ubuntu 24.04 x86_64.
Solo dopo il successo di tutti i build pubblica una prerelease con archivi,
rapporti dei moduli e checksum SHA-256. La patch attuale è sperimentale:
la riduzione richiesta a 500 righe è ancora da completare.

I riferimenti sono i **binari ufficiali**, scaricati con versioni e hash fissati
in [tools/releases.json](tools/releases.json). Il build legge le opzioni da
`-V`, configura il sorgente originale, applica la patch e confronta le tabelle
dei moduli. L'unica aggiunta ammessa è `ngx_http_symlink_access_module`;
verifica anche le opzioni di moduli e funzionalità del binario finale.
I rapporti `*-modules.json` e i file `official-V.txt`/`patched-V.txt` documentano
il confronto. Il riferimento è il pacchetto base: i moduli dinamici distribuiti
separatamente non sono inclusi né attivati automaticamente.

I binari Linux richiedono Ubuntu 24.04 x86_64 o un ambiente compatibile e le
librerie indicate in `dependencies.txt`; mantengono i percorsi e l'utente dei
pacchetti ufficiali. Per sostituire un'installazione, occorrono quindi anche
la configurazione e gli utenti predisposti dal relativo pacchetto.
Il binario Windows usa MSVC x86 e le stesse versioni di OpenSSL, PCRE2 e zlib
della distribuzione nginx ufficiale. Angie non ha un equivalente Windows
ufficiale e la sua build Windows upstream ha errori: non si pubblica un
eseguibile Angie Windows non verificato.

Per avviare: GitHub → Actions → ci → Run workflow. Localmente, `bash build.sh`
su Ubuntu 24.04; su Windows servono Git Bash, Python, Strawberry Perl e
l'ambiente MSVC x86. Gli archivi vengono scritti in `dist/`.

## Applicazione

La stessa patch, senza varianti, è verificata sui seguenti commit:

| Server | Commit |
| --- | --- |
| nginx | `ef0aa967dce9d30b824d4c839d3579d2a17e0666` |
| Angie | `417125cb8664863b044c8c56f0f8d6135bc36b6d` |

Dentro il checkout del server:

```sh
git apply --check /percorso/nginx-symlink/patches/symlink-access.patch
git apply /percorso/nginx-symlink/patches/symlink-access.patch

# nginx
./auto/configure <opzioni-di-build>
make

# Angie: usare invece ./configure <opzioni-di-build>
```

Mantenere le opzioni di compilazione richieste dall'installazione. Per release
diverse usare prima `git apply --check` ed eseguire i test: non è garantita
l'applicabilità a qualsiasi versione futura o precedente.

Le strutture e la firma dei moduli restano quelle originali: la patch non
impone la ricompilazione dei moduli dinamici già compatibili con la specifica
build del server. Il test ABI verifica il caricamento di un modulo compilato
con gli header originali. I moduli esterni che aprono direttamente file non
diventano automaticamente protetti.

L'integrazione consiste in una funzione HTTP di apertura dedicata, richiamata
nei punti che già aprono i contenuti. Il contesto privato viene passato sullo
stack; non è aggiunto alle strutture del server, né conservato globalmente.
Il codice e le API della cache dei file del server restano intatti.

## Configurazione

```nginx
http {
    symlink_access_cache max=1024 valid=30s negative_valid=1s;

    server {
        root /srv/www/site/public;
        symlink_access root_owner;

        # Opzionale, utile con alias verso un file o root amministrative:
        # symlink_access_root /srv/www/site;
    }
}
```

Su Windows, per esempio `root C:/sites/example/public;`.

| Direttiva | Contesto | Default |
| --- | --- | --- |
| `symlink_access off\|root_owner` | http, server, location | `off` |
| `symlink_access_root <directory con eventuali variabili>` | http, server, location | `$document_root` |
| `symlink_access_cache off` oppure `max=N valid=tempo negative_valid=tempo` | http | `max=1024 valid=30s negative_valid=1s` |

I parametri della cache sono opzionali, non ripetibili. `max` è compreso tra
1 e 65536; i tempi sono in secondi, con la sintassi nginx per le durate.
`valid=0s` forza il refresh a ogni uso. Le direttive di location si ereditano;
`symlink_access off` permette di disattivare il controllo localmente.

## Regola applicata

Quando almeno un componente è un symlink, il file finale deve risultare
leggibile secondo questa regola:

1. L'utente della webroot è owner del target: usare i bit owner.
2. Altrimenti, un suo gruppo corrisponde a un gruppo del target: usare i bit
   dei gruppi corrispondenti (un solo GID proprietario su Unix).
3. Altrimenti: usare i bit other.

Nessun ripiego su other se owner o group corrispondono ma non hanno lettura.
Il gruppo primario e i supplementari sono inclusi. Nessun privilegio speciale
per UID 0. Identità/gruppi non ricavabili, liste oltre 4096 gruppi, o permessi
non rappresentabili comportano **accesso negato**.

Per `link_directory/file.txt` viene verificato file.txt, non soltanto la
directory puntata. Il controllo riguarda il descriptor/handle poi usato per
leggere il contenuto. I percorsi senza link non ricevono questo controllo
aggiuntivo sui bit; rimangono soggetti ai controlli nativi del worker e alle
restrizioni del resolver della piattaforma.

`disable_symlinks` continua a valere dove disponibile: la nuova policy non
annulla un divieto imposto dalla direttiva esistente. La directory da cui si
ricava l'identità deve essere gestita amministrativamente, non sostituibile dal
tenant con una directory di un altro utente.

## Cache e costo

La cache è per worker, con una tabella hash a numero fisso di slot.
Una collisione espelle la voce precedente; si confronta sempre l'ID completo,
quindi una collisione può causare un nuovo lookup, mai un'autorizzazione errata.
Contiene solo `identità -> gruppi`, compresi i lookup falliti con TTL separato.
La scadenza è assoluta: un hit non la rinnova. Cambi di appartenenza diventano
visibili dopo il TTL, oltre ai ritardi delle eventuali cache NSS/AD.

Non si memorizzano verdetti di accesso o permessi del target. Le aperture
protette bypassano `open_file_cache`, anche se il percorso non contiene link:
una cache già calda in un'altra location non può saltare la policy. I log
dinamici non sono aperture di contenuto e conservano il comportamento normale.

I lookup mancanti in cache sono sincroni. NSS/AD e la classificazione dei SID
Windows possono quindi causare latenza; non è implementato un resolver asincrono.
Non sono ancora disponibili benchmark di throughput o latenza.

## Portabilità e limiti

Unix usa `fstat`, `getpwuid_r` e `getgrouplist`, con verifica in configure e
gestione della firma `int` di Darwin. Il resolver usa aperture relative a directory
e `O_NOFOLLOW`. Senza le API necessarie il controllo nega l'accesso.
I bit Unix vengono usati deliberatamente senza valutare ACL estese,
capabilities, SELinux/AppArmor o i diritti del tenant su tutte le directory.

Windows usa SID, elenco dei gruppi via Authz e owner/DACL letti dall'handle.
Le aperture relative con `NtCreateFile` mantengono il percorso legato alle
directory aperte; symlink e junction sono riconosciuti. La normalizzazione
accetta ACE allow per owner, gruppi ed Everyone, con bit r/w/x ridotti.
ACE deny o altre forme non rappresentabili negano l'accesso. Everyone si applica
anche alle classi owner/group; DACL vuota e NULL DACL sono distinte. Un owner
che è un gruppo non identifica un utente e non viene indovinato.

La policy Windows può negare accessi che Windows permetterebbe: in particolare
si conserva la precedenza owner del modello comune. Non replica un token di
logon completo, ACL SMB o altri privilegi. I percorsi protetti richiedono
`READ_CONTROL` sul file; componenti `.`/`..`, stream alternativi e namespace
device espliciti vengono negati. NTFS locale è il filesystem verificato;
scenari AD/SMB remoti non sono ancora testati.

Sono integrati static, index, try_files, gzip_static, FLV e MP4 attraverso il
percorso comune. I gestori autoindex, random_index e DAV rispondono 403 quando
selezionati in una location con la policy attiva: non sono implementati elenchi
o scritture protette. `try_files` può saltare un candidato negato e usare il
fallback configurato; il divieto non obbliga ogni handler a terminare con 403.

Non copre hard link o file aperti da PHP-FPM/altri upstream. I permessi non sono
congelati per l'intera durata della risposta. I moduli esterni richiedono audit
se non passano dalle funzioni di apertura HTTP integrate.

## Verifica

| Piattaforma | nginx | Angie |
| --- | --- | --- |
| Linux glibc, Ubuntu/WSL2 | Build e test HTTP | Build e test HTTP |
| Linux musl, Alpine/WSL2 | Build e test HTTP | Build e test HTTP |
| Windows nativo, build MinGW-w64 | Build ed esecuzione HTTP su NTFS | Nuovi oggetti compilati; build completa bloccata a monte |
| FreeBSD / macOS | Backend previsto, esecuzione non verificata | Backend previsto, esecuzione non verificata |

**Angie/Windows:** il commit originale, anche senza patch, non compila nel
toolchain verificato (`ngx_init_setproctitle`, `ngx_update_process_title` e
problemi in `ngx_log.c`). Non viene dichiarato il supporto dell'eseguibile
Angie su Windows fino alla risoluzione e al test di quella build.

Test riproducibili, dopo aver compilato con DAV, random_index, gzip_static, FLV,
MP4 e rewrite abilitati su Unix:

```sh
sudo python3 tests/integration_unix.py /percorso/nginx/objs/nginx
sudo python3 tests/integration_unix.py /percorso/angie/objs/angie
sh tests/run_cache_policy.sh /percorso/nginx
python3 tests/module_abi.py /percorso/nginx
```

Il test Unix modifica solo ownership e permessi di file temporanei, senza
creare o modificare account. Comprende sostituzioni concorrenti dei link,
chmod, cache riscaldata, owner/group/other, gruppi supplementari se disponibili,
alias, root variabile, redirect interno, index, try_files e log dinamici.
Il test della cache usa lookup deterministici e AddressSanitizer/UBSan.

```powershell
./tests/integration_windows.ps1 -Binary C:/percorso/nginx.exe
```

Il test Windows usa ACL e junction temporanee; testa anche symlink a file
quando il sistema ne permette la creazione. Il rapporto finale è in
[TESTING.md](TESTING.md). [DESIGN.md](DESIGN.md) conserva il ragionamento iniziale.
