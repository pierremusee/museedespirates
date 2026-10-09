// Reprise d'un paiement web dont l'issue est incertaine (timeout,
// réponse perdue, rechargement de page).
//
// L'opération en attente est persistée dans sessionStorage — clé
// d'idempotence + identifiant de réservation, aucune donnée sensible.
// Cycle de vie :
//   création   → après POST /reservations réussi, avant le paiement ;
//   reprise    → nouvelle tentative : même réservation, même clé ;
//   succès     → nettoyage ;
//   rejet 4xx  → la clé est libérée (paymentKey=null) : une nouvelle
//                tentative sur la même réservation est une nouvelle
//                opération ; la réservation pending n'est pas recréée ;
//   échec ambigu (réseau, 5xx) → conservée telle quelle.

export type PendingPayment = {
  reservationId: string;
  productCode: string;
  paymentKey: string | null;
  amount: string;
};

const STORAGE_KEY = "musee:pending-payment";

export function readPendingPayment(
  storage: Pick<Storage, "getItem">
): PendingPayment | null {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingPayment;
    if (!parsed.reservationId || !parsed.amount) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function savePendingPayment(
  pending: PendingPayment,
  storage: Pick<Storage, "setItem">
): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(pending));
  } catch {
    // Quota/mode privé : la reprise en mémoire suffit pour la session.
  }
}

export function clearPendingPayment(
  storage: Pick<Storage, "removeItem">
): void {
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // noop
  }
}

// Décision au moment d'une nouvelle soumission.
export type PendingAction =
  | "resume" // même produit : reprendre le paiement de la réservation
  | "check_previous" // autre produit : vérifier l'ancienne réservation
  | "fresh"; // aucune opération en attente

export function pendingAction(
  pending: PendingPayment | null,
  productCode: string
): PendingAction {
  if (pending === null) return "fresh";
  if (pending.productCode === productCode) return "resume";
  return "check_previous";
}

// Après GET /reservations/{id} de l'ancienne opération (autre produit).
export type PreviousOutcome = "conclude" | "abandon";

export function previousOutcome(
  status: string | null
): PreviousOutcome {
  // Confirmée : le paiement avait abouti — conclure sur cette commande.
  // pending/cancelled/expired/injoignable : abandonner, la purge TTL
  // restituera la jauge ; la nouvelle opération peut démarrer.
  return status === "confirmed" ? "conclude" : "abandon";
}
