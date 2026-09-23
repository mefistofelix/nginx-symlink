# Controllo mirato dei symlink per nginx e Angie

Stato: documento di progettazione, seguito dall'implementazione in
`patches/symlink-access.patch`. Configurazione, comportamento effettivo, differenze
rispetto alla proposta e piattaforme verificate sono descritti in README.md.

**Scelta consigliata: patch al percorso di apertura dei file, con una direttiva
nuova e logica comune ai due server.** La portabilità richiede due backend,
Unix e Windows; non richiede una simulazione completa dei rispettivi sistemi di
autorizzazione.

## Requisito concordato

Usare il proprietario della webroot della richiesta come identità di riferimento
per decidere se consentire la lettura di un file raggiunto tramite symlink.
Il controllo è aggiuntivo rispetto all'apertura effettuata dal worker.

Su Unix valutare soltanto UID, GID e bit di permesso del target, con i gruppi
primario e supplementari dell'utente di riferimento. Come chiarito nella
conversazione, consentire anche attraverso i bit `other` quando quella è la
classe applicabile. Non ricostruire tutti i controlli che il kernel eseguirebbe
per quell'utente.

Sono quindi fuori dal controllo proposto: ACL Unix, capabilities, SELinux,
AppArmor, privilegi di root, impersonificazione e verifica di ogni directory
come se il worker fosse l'utente della webroot. Il sistema operativo continua
comunque a imporre i propri controlli sull'identità effettiva del worker.

## Perché una patch

Un modulo HTTP nella fase access può controllare un pathname, ma il modulo che
serve il contenuto lo apre successivamente: il symlink può cambiare tra le due
operazioni. Inoltre index, try_files e open_file_cache hanno percorsi propri.
Un modulo che servisse direttamente i file potrebbe conservare il descriptor
controllato, ma dovrebbe duplicare e mantenere parti del comportamento statico.

Il punto adatto è l'apertura effettiva: controllare i metadati del descriptor
che sarà poi usato per inviare il contenuto. Non usare una sequenza
`realpath -> stat -> autorizzazione -> nuova apertura per nome`.

La patch dovrebbe contenere tre parti separabili: configurazione HTTP e identità
della richiesta; controllo del file e interazione con la cache; backend del
sistema operativo. Non serve introdurre un sistema generico di hook o un ABI
pubblico per la prima implementazione.

## Regola Unix

Un inode ha un solo gruppo proprietario (`st_gid`); i gruppi multipli appartengono
all'utente. Partendo da `st_uid` della webroot, risolvere l'account con
`getpwuid_r` e ottenere il gruppo primario più i supplementari con
`getgrouplist`, dove disponibile. Verificare le API in configure e adattare le
firme alle piattaforme: getgrouplist non è un'interfaccia POSIX universale.

```text
se uid_webroot == target.st_uid:
    consentito = target.st_mode contiene S_IRUSR
altrimenti se target.st_gid appartiene ai gruppi di uid_webroot:
    consentito = target.st_mode contiene S_IRGRP
altrimenti:
    consentito = target.st_mode contiene S_IROTH
```

Le classi sono alternative: se corrisponde owner ma manca il suo bit di lettura,
non si ripiega su group o other. Analogamente, un gruppo corrispondente senza
lettura non permette di usare other. Nessuna eccezione automatica per UID 0.

Esempi, con identità della webroot `alice`, gruppi `{users, shared}`:

| Target | Mode | Esito |
| --- | --- | --- |
| `bob:shared` | `0640` | Consenti tramite group |
| `bob:private` | `0640` | Nega |
| `bob:private` | `0644` | Consenti tramite other |
| `alice:shared` | `0040` | Nega: la classe owner prevale |
| `bob:shared` | `0604` | Nega: la classe group prevale |

Con ACL estese presenti, questa resta deliberatamente una regola sui mode bit,
non una risposta alla domanda «alice potrebbe davvero aprire questo file?».
In particolare i bit group possono rappresentare la maschera ACL. Il limite
deve essere esplicito nella documentazione, senza aggiungere un motore ACL Unix.

## Quale target controllare

Per la prima versione, il controllo di lettura riguarda il **file finale**
quando almeno un componente del percorso è un symlink. Vale anche per
`webroot/link_directory/file.txt`: il target da controllare è file.txt.
Non basta autorizzare la directory puntata dal link.

Il resolver deve ricordare se ha attraversato un link e mantenere aperte le
directory necessarie. Su Unix tentare prima un'apertura senza seguire link;
se occorre seguirne uno, conservare l'indicazione anche se il nome viene
sostituito prima dell'apertura successiva. Con O_PATH, O_NOFOLLOW può restituire
un descriptor del link: occorre distinguerlo tramite fstat, non solo errno.
Nessuna autorizzazione dipende da un lstat successivo che dica «non è un link».

Le directory sondate da index/try_files non costituiscono un'autorizzazione al
file finale: il controllo viene eseguito quando quel file è aperto. In questa
versione non aggiungere la simulazione del diritto di attraversamento delle
directory. Il supporto agli elenchi di directory richiederebbe una regola distinta.

Il file deve essere quello del descriptor verificato fino all'invio, incluso
sendfile. Il controllo non promette di congelare permessi o contenuto durante
l'intera risposta. Non comprende hard link né aperture effettuate da PHP-FPM
o da altri upstream.

## Identità della webroot

Usare la root effettiva della location corrente, valutando le variabili nel
contesto della richiesta. Aprire la directory e leggerne l'owner dal descriptor;
il proprietario del symlink stesso non è l'identità cercata.

Per alias che indica un file, e per configurazioni in cui la directory dei
contenuti non rappresenta il proprietario del sito, permettere una directory
di riferimento esplicita. Ricalcolare il contesto dopo internal redirect e
per ogni subrequest; non ereditare ciecamente l'identità dal primo URI.

La directory di riferimento e il suo collegamento al sito devono essere sotto
controllo amministrativo. Se un tenant può sostituirla con un link verso una
directory di un altro utente, può alterare l'identità ricavata dal suo owner.
Non considerare questo schema una sandbox del filesystem.

## Interfaccia normalizzata

Due operazioni di piattaforma, con gli stessi risultati logici su Unix e Windows:

```text
get_user_groups(user_id) -> group_id[]

get_path_access(open_handle) -> {
    owner_id,
    owner_bits: rwx,
    groups: [{ group_id, bits: rwx }],
    other_bits: rwx,
    usable: boolean
}
```

Gli ID sono opachi, con tipo, confronto per uguaglianza e hash: UID/GID numerici
su Unix, SID su Windows. Non usare i nomi come chiave di sicurezza. Su Unix
groups contiene esattamente il GID proprietario; Windows può restituire più
gruppi. Nella classe group, la regola comune usa l'unione dei bit dei gruppi
corrispondenti. Owner ha precedenza; other si usa solo in assenza di owner e
gruppi corrispondenti. Per servire il file è richiesto il bit r.

get_path_access interroga il descriptor/handle del target aperto, evitando di
rileggere un pathname che può essere cambiato. La stessa primitiva ricava
l'owner della directory di riferimento. La policy e la cache non conoscono
stat, NSS, SID, DACL o Authz: sono dettagli dei backend. Si tratta di due
operazioni dell'interfaccia, non necessariamente di due sole chiamate native.

Se i permessi non sono rappresentabili o non sono ricavabili, usable è false:
il controllo restituisce semplicemente **accesso negato**, senza ripiegare
sulla classe other e senza generare un errore HTTP 500 per questo caso.
Anche una lista gruppi non risolvibile impedisce l'accesso: non equivale a una
lista vuota valida. Gli eventuali dettagli restano diagnostica interna.

## Backend Windows del modello ridotto

Leggere owner e DACL con GetSecurityInfo sull'handle e proiettarli nel formato
comune. Non introdurre AuthzAccessCheck come secondo motore di policy.
La scoperta dei gruppi può usare API Authz, ma restituisce una lista normalizzata:
solo gruppi abilitati utilizzabili per concessioni, senza trattare SID disabled
o deny-only come gruppi che concedono accesso.

Definire una proiezione conservativa delle DACL semplici:

- SID owner utente -> owner_id; ACE allow per quell'utente -> owner_bits.
- ACE allow per gruppi identificati -> entries in groups; più ACE per lo stesso
  gruppo sono aggregate. Concessioni ad altri utenti specifici non sono
  rappresentabili nell'interfaccia proposta.
- ACE allow per Everyone -> other_bits; quei bit si aggiungono anche a owner_bits
  e alle entries dei gruppi, poiché Everyone comprende quelle classi.
- Solo ACE applicabili all'oggetto: ignorare inherit-only e includere le ACE
  ereditate applicabili al target. Espandere i generic rights.
- r = FILE_READ_DATA (FILE_LIST_DIRECTORY sulle directory), w = FILE_WRITE_DATA
  (FILE_ADD_FILE), x = FILE_EXECUTE (FILE_TRAVERSE). Sono bit del modello ridotto:
  w non promette append, cancellazione o modifica delle ACL.
- DACL vuota = zero concessioni; NULL DACL = rwx per tutti. Una lettura fallita
  della DACL non equivale a una NULL DACL.
- ACE deny, condizionali, object-specific e altri costrutti non implementati,
  compresi trustee non classificabili: usable=false, dunque accesso negato.
  Non ignorare ACE che potrebbero restringere i permessi.

La precedenza owner del modello comune può negare un accesso che Windows
concederebbe tramite un gruppo dell'owner. È un limite esplicito della proiezione,
che non deve concedere bit vietati dalla DACL supportata. Verificarlo con test.
Niente ricostruzione di ACL SMB, privilegi o altre policy Windows; il controllo
nativo sull'identità del worker continua comunque ad applicarsi.

Se l'owner è un gruppo, per esempio Administrators, manca un utente univoco di
cui ricavare l'appartenenza ai gruppi. Non scegliere arbitrariamente un membro:
occorre una directory di riferimento con owner utente o una futura mappatura
amministrativa esplicita. Se l'identità non è risolvibile, negare l'accesso.
La lettura del security descriptor richiede READ_CONTROL sul relativo handle.

Rilevare anche junction e reparse point intermedi. FILE_FLAG_OPEN_REPARSE_POINT
sul solo file finale non basta a proteggere gli antenati. Il prototipo Windows
deve dimostrare la stabilità del percorso con aperture relative a handle o
un'altra strategia verificata; tipi di reparse point non supportati vanno negati.
Questa è la parte da validare prima di dichiarare compatibilità Windows.

## Cache configurabile

La prima cache utile è **identità -> gruppi**, non pathname -> accesso consentito.

| Dato | Proposta iniziale | Motivo |
| --- | --- | --- |
| ID utente -> lista normalizzata di gruppi | TTL 30s, limite 1024 identità per worker | Evitare lookup ripetuti NSS/AD |
| Identità non risolvibile | TTL 1s | Limitare richieste ripetute senza conservare a lungo gli errori |
| Owner della directory di riferimento | Solo per l'operazione corrente | Rilevare cambi di owner senza un secondo TTL |
| Target, permessi, verdetto | Nessuna cache aggiuntiva | Non conservare autorizzazioni dopo chmod/chown o sostituzioni |

I numeri sono valori di partenza da misurare. TTL assoluto dal lookup, non
rinnovato a ogni hit; tabella hash limitata, con espulsione sulle collisioni;
limiti anche al numero di
gruppi e alla memoria. Non usare risultati troncati. Snapshot immutabili per le
operazioni in corso, rilascio della memoria all'espulsione, cache vuota nei
nuovi worker dopo reload. Un reload graceful non elimina istantaneamente le
cache dei vecchi worker ancora in esecuzione.

Una revoca di appartenenza a un gruppo può diventare visibile dopo il TTL, oltre
all'eventuale ritardo di NSS/SSSD/AD. Mai riutilizzare una voce scaduta in caso
di errore di refresh. Gli errori impediscono il rilascio del contenuto protetto.

Per semplicità la cache è per worker e contiene solo ID normalizzati: niente
memoria condivisa. Il costo in memoria si moltiplica per il numero di worker.

Una cache non rende asincrono un cache miss. getgrouplist e la risoluzione SID
possono bloccare, soprattutto con directory remote. Per la prima versione
minimale il lookup sincrono deve essere documentato e misurato; se i miss sono
costosi, introdurre un resolver asincrono prima dell'apertura HTTP, con coda
limitata e richieste duplicate accorpate. Non fingere di imporre un timeout
portabile a getgrouplist con un semplice parametro di configurazione.

## Interazione con open_file_cache

La cache nginx può restituire un descriptor senza richiamare l'apertura.
Aggiungere il controllo soltanto in ngx_open_file_wrapper sarebbe insufficiente.

Prima versione: bypass della open_file_cache per tutte le aperture con la nuova
policy attiva, anche quando il percorso alla fine non contiene link. In questo
modo non occorre conservare e invalidare l'intera storia di risoluzione del
percorso. Le location senza policy mantengono il comportamento esistente.

Una successiva ottimizzazione dovrà considerare identità, versione dei gruppi,
policy, percorso, cambio dei link e metadati; fstat del solo descriptor cached
non rileva il retargeting del pathname né ricostruisce se vi fossero symlink.

## Sintassi proposta

```nginx
http {
    symlink_access_cache max=1024 valid=30s negative_valid=1s;

    server {
        root /srv/www/site/public;
        symlink_access root_owner;

        # Opzionale: directory da cui ricavare l'identità del sito.
        # symlink_access_root /srv/www/site;
    }
}
```

`symlink_access off|root_owner`: default off, contesti http/server/location.
`symlink_access_root`: espressione di directory opzionale, stessi contesti.
`symlink_access_cache`: configurazione HTTP della cache per worker; prevedere
anche off. I nomi non sono direttive esistenti.

Le restrizioni di disable_symlinks continuano a valere quando entrambe sono
attive: il nuovo controllo non deve rendere consentito ciò che la direttiva
esistente vieta. Non aggiungere implicitamente un from=$document_root che salti
parti del controllo. Le strutture della nuova policy devono esistere anche
fuori da NGX_HAVE_OPENAT per poter supportare Windows.

## Compatibilità e verifica

Sorgenti ispezionati, checkout locali in upstream/:

- nginx: ef0aa967dce9d30b824d4c839d3579d2a17e0666.
- Angie: 417125cb8664863b044c8c56f0f8d6135bc36b6d.

Nei due checkout ngx_open_file_cache.c differisce soltanto per il nome del
server in un commento. È una buona base per condividere il codice, non una
garanzia di applicabilità su ogni release. Mantenere patch verificabili sui
commit supportati, logica comune e build separate.

L'implementazione mantiene intatti ngx_open_file_info_t, ngx_open_cached_file,
ngx_open_file_wrapper, ngx_http_set_disable_symlinks e la firma ABI originale.
Static, index, try_files, gzip_static, FLV e MP4 chiamano una funzione HTTP
dedicata, che passa esplicitamente un contesto privato ai backend. Non ci sono
campi aggiunti alle strutture esistenti né contesti globali della richiesta.

Autoindex, random_index e DAV non sono automaticamente coperti dal percorso
esistente: nell'implementazione i relativi handler negano l'accesso (403) quando
effettivamente selezionati in una location con la nuova policy attiva.
I moduli esterni che aprono file direttamente devono essere integrati a parte.

Test necessari prima del rilascio: classi owner/group/other e precedenze;
gruppo primario e supplementari; cambio gruppi e scadenza cache; UID/SID non
risolvibile; symlink a file e directory; catene, loop e link rotti; sostituzioni
concorrenti; chmod/chown; cache nginx già calda; alias, root variabile,
internal redirect e subrequest; integrazione dei moduli supportati.
Windows: proiezione ACE allow ed Everyone, diniego per ACE non rappresentabili,
DACL vuota e NULL, gruppi enabled/disabled/deny-only, owner utente/gruppo,
junction, reparse point intermedi, errori AD e sostituzioni concorrenti.
Non limitarsi a un test di compilazione.

Matrice prevista: Linux glibc/musl, FreeBSD, macOS e Windows nativo, su entrambi
i server. La presenza del codice win32 in Angie non prova che quella build sia
testata o distribuita ufficialmente: va verificata nel progetto.

Le build e i test successivi alla progettazione sono riportati in TESTING.md.
Non sono state eseguite misure prestazionali.

## Riferimenti

- [disable_symlinks](https://nginx.org/en/docs/http/ngx_http_core_module.html#disable_symlinks)
- [nginx: apertura e cache, commit ispezionato](https://github.com/nginx/nginx/blob/ef0aa967dce9d30b824d4c839d3579d2a17e0666/src/core/ngx_open_file_cache.c)
- [Angie: apertura e cache, commit ispezionato](https://github.com/webserver-llc/angie/blob/417125cb8664863b044c8c56f0f8d6135bc36b6d/src/core/ngx_open_file_cache.c)
- [getgrouplist](https://man7.org/linux/man-pages/man3/getgrouplist.3.html)
- [access e identità del chiamante](https://man7.org/linux/man-pages/man2/access.2.html)
- [ACL e mode bit](https://man7.org/linux/man-pages/man5/acl.5.html)
- [GetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo)
- [AuthzInitializeContextFromSid](https://learn.microsoft.com/en-us/windows/win32/api/authz/nf-authz-authzinitializecontextfromsid)
- [AuthzAccessCheck](https://learn.microsoft.com/en-us/windows/win32/api/authz/nf-authz-authzaccesscheck)
- [Semantica DACL](https://learn.microsoft.com/en-us/windows/win32/secauthz/how-dacls-control-access-to-an-object)
- [Diritti di accesso Windows](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)
- [Reparse point e operazioni sui file](https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points-and-file-operations)
