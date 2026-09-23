# Scelte della versione minima

Il modulo è un unico file C: configurazione, cache e adattamenti Unix/Windows.
Non modifica `ngx_http_request_t`, `ngx_open_file_info_t`, configurazioni core,
firma ABI o API esistenti. Le strutture aggiunte sono private al modulo.

I punti HTTP di apertura passano esplicitamente la richiesta alla funzione
wrapper. Questo mantiene corretta l'identità per alias, variabili e redirect
interni, senza contesto globale temporaneo o dati nascosti in campi esistenti.
Le sostituzioni nei moduli sono piccole, ma vanno mantenute in ogni percorso
che può inviare un file. I moduli di directory/DAV hanno guardie dedicate.

## Due operazioni normalizzate

`ngx_sa_user_groups` raccoglie i gruppi del proprietario della webroot.
`ngx_sa_permissions` restituisce i bit rwx applicabili con precedenza
owner/group/other, oppure diniego. Le rappresentazioni native UID/GID e SID
restano interne ai rami di piattaforma dello stesso file.

La tabella di cache è diretta, a dimensione fissa configurabile. Ogni voce
possiede un pool liberato alla scadenza, alla collisione o alla distruzione del
ciclo. Il TTL non scorre sugli hit e parte prima del lookup. Errori e utenti
non risolti vengono negati e non restano in cache.

## Identità del file servito

Unix prova `ngx_open_cached_file(NULL, ...)` con `disable_symlinks on`, senza
prefisso escluso. Se riesce, serve quel descriptor. Se incontra un link, ripete
l'apertura rispettando la configurazione originale e controlla quel descriptor.
La gestione di metadata, directio, read-ahead e cleanup rimane al server.

Windows apre senza reparsing sotto l'handle del volume/share, poi usa l'apertura
standard nginx per mantenere la validazione dei nomi e la gestione dei file.
Il primo handle rimane aperto durante il confronto dei due ID file a 128 bit e
dei volumi. Oggetti diversi, reparsing o identità non confrontabile richiedono
il controllo dei permessi sull'handle servito. Non si riusa un'autorizzazione
basata su un path precedente o su una voce di `open_file_cache`.

Le directory sono sonde per index/try_files; l'autorizzazione avviene sul file
finale. Autoindex, random_index e DAV non possono usare questa eccezione per
leggere o modificare contenuti nelle location protette.

La soluzione è un controllo aggiuntivo mirato, non un isolamento completo dei
tenant: hardlink, ACL complete, accesso tramite backend e moduli esterni restano
fuori dal modello richiesto. I limiti operativi sono riportati nel README.
