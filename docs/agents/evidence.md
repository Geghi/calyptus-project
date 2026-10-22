# Evidenze per la review

Ogni branch pronto per la review porta la prova che il cambio funziona. Chi la legge non testa a mano.
L'agente sceglie il livello, al massimo tre prove, proporzionali al cambio.

## Come si avvia e si testa

Nessuno stack ancora: i comandi si aggiungono quando lo spec sceglie lo stack.
Finché mancano, dillo nel report: niente comandi inventati.

- Avvio in locale: non definito
- Unit: non definito

## Quanto testare — i tre livelli

### Livello 0 — una riga, testo, config

Il nome del test rosso→verde e cosa verifica, in una riga.

### Livello 1 — logica o endpoint

Livello 0 + un esempio reale
(es. richiesta/risposta vera, ritorno di un tool, prima/dopo).

### Livello 2 — feature visibile o udibile

Livello 1 + screenshot o breve registrazione del percorso principale,
salvato in `$(git rev-parse --git-dir)/ship-evidence/` e citato col path assoluto.

## Dove va

Niente PR: l'evidenza va nel report di `/ship` e nel commento del ticket.
Sezione `## Evidence`: livello scelto, comandi lanciati, prova. Nient'altro.
Per produrre l'evidenza lancia solo i test che servono a produrla, non
l'intera suite.
