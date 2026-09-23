# symlink_access per nginx e Angie

Una patch comune controlla la lettura dei file raggiunti tramite symlink usando
il proprietario della webroot corrente come identità di riferimento.
**Nessuna modifica alle strutture esistenti o alla firma ABI.**

[Patch](patches/symlink-access.patch) · [Release](https://github.com/mefistofelix/nginx-symlink/releases)
· [Workflow](.github/workflows/ci.yml) · [Verifiche](TESTING.md)

## Configurazione

```nginx
http {
    symlink_access_cache 1024 30s;
    server {
        root /srv/www/site/public;
        symlink_access on;
    }
}
```

Su Windows: `root C:/sites/example/public;`.

- `symlink_access on|off`: contesti http/server/location, ereditata, default off.
- `symlink_access_cache <voci> <durata>`: contesto http, default `1024 30s`.
  Zero voci o durata zero disabilitano la cache; massimo 65536 voci.

La webroot è il valore corrente di `$document_root`, inclusi root variabili e
alias verso directory. Deve essere una directory con proprietario identificabile.
Un alias verso un singolo file non fornisce tale directory: l'accesso al file
tramite symlink viene negato. Non esiste una direttiva separata per sovrascrivere
l'identità della webroot.

La configurazione sperimentale precedente (`root_owner`, `max=`, `valid=`,
`negative_valid=` e `symlink_access_root`) è stata rimossa nella semplificazione.

## Controllo mirato

Il controllo considera i bit di lettura del **file effettivamente aperto**:
owner se coincide con il proprietario della webroot, altrimenti gruppi di tale
utente, altrimenti other. Una categoria selezionata senza lettura non passa
alla categoria successiva. Non simula le credenziali del kernel, il percorso
completo, SELinux, AppArmor o tutte le ACL. Anche il processo server deve poter
aprire il file con le proprie credenziali.

Unix usa UID, `getpwuid_r`, `getgrouplist` e `fstat`. I bit group dei file con
ACL POSIX possono rappresentare la maschera ACL: questa policy non sostituisce
una verifica completa delle ACL. Sono ammessi fino a 4096 gruppi e un record
utente NSS fino a 16 KiB; lookup mancanti o non rappresentabili negano l'accesso.

Windows usa SID, Authz e DACL. Traduce le ACE allow in bit rwx per owner, gruppi
abilitati ed Everyone. ACE deny applicabili, tipi di ACE non riconosciuti e
utenti aggiuntivi non rappresentabili comportano diniego. Le ACE inherit-only
sono ignorate; NULL DACL e DACL vuota restano distinte. Non è una simulazione
completa di un token Windows o del suo controllo ACL.

La cache riguarda solo le appartenenze ai gruppi, è separata per worker e usa
scadenza assoluta. Le collisioni espellono una voce e confrontano sempre tutta
l'identità; i lookup falliti non vengono memorizzati. Proprietari e permessi
dei file vengono riletti. Una revoca di gruppo può impiegare fino al TTL per
diventare effettiva nella policy.
Un lookup NSS/AD lento può bloccare il worker quando manca in cache.

Per i percorsi protetti, `open_file_cache` non viene usata. Unix riusa l'apertura
senza symlink del server. Windows usa `OBJ_DONT_REPARSE`, mantiene aperto il
descriptor di confronto e confronta volume e ID file a 128 bit con il file
aperto dal server. Se il percorso cambia oggetto, controlla i permessi del
descriptor servito. Il codice non autorizza con un semplice controllo del path
seguito da una riapertura non verificata.

Sono coperti static, index, try_files, gzip_static, FLV e MP4. Nelle location
protette, autoindex, random_index e le operazioni DAV abilitate sono negate.
PHP-FPM, upstream e moduli esterni che aprono autonomamente file non sono coperti.
`disable_symlinks` continua ad applicarsi su Unix; i file diretti non cambiano
policy. Un diniego della nuova policy produce 403; `try_files` può scegliere il
fallback configurato. File inesistenti restano soggetti alle normali risposte 404.

## Compilazione e release

Applicare la stessa patch a entrambi i server:

```sh
git apply --check /percorso/symlink-access.patch
git apply /percorso/symlink-access.patch
./auto/configure <opzioni>  # nginx da Git; ./configure per tarball e Angie
make
```

La patch richiede la ricompilazione del server. Non è un modulo dinamico da
caricare in un eseguibile esistente; non cambia la compatibilità ABI dei moduli
già compatibili con la specifica versione e configurazione del server.

Il workflow manuale `ci` compila nginx 1.31.6 e Angie 1.12.2 per Ubuntu 24.04
x86_64, e nginx 1.31.6 per Windows x86 con MSVC. Confronta le opzioni dei binari
ufficiali e le tabelle dei moduli prima/dopo la patch: l'unica aggiunta ammessa
è `ngx_http_symlink_access_module`. I moduli dinamici distribuiti separatamente
non fanno parte del binario base e non vengono inclusi o attivati.

Versioni, URL e SHA-256 sono fissati in [tools/releases.json](tools/releases.json).
Tutti i job devono superare i test prima della pubblicazione della prerelease.
Gli archivi contengono binari, configurazioni, licenze, patch, output `-V` e
rapporto dei moduli; la release aggiunge i checksum degli archivi.

Linux richiede le librerie in `dependencies.txt` e conserva percorsi e utenti
dei pacchetti ufficiali: predisporre configurazione e account dell'installazione.
Windows include staticamente OpenSSL 3.5.8, PCRE2 10.48 e zlib 1.3.2, come il
binario ufficiale di riferimento. Non si distribuisce Angie Windows: manca un
equivalente ufficiale e la build upstream Windows presenta errori indipendenti.

Avvio: GitHub → Actions → ci → Run workflow, oppure `bash build.sh` su Ubuntu
24.04. Su Windows servono Git Bash, Python, Strawberry Perl e ambiente MSVC x86.
Output in `dist/`. FreeBSD, macOS, domini AD e share SMB non sono stati validati.
