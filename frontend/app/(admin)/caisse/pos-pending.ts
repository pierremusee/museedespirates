// Reprise d'un encaissement POS dont l'issue est incertaine (timeout,
// réponse perdue, rechargement de la page de caisse).
//
// Persisté dans sessionStorage — identifiant de réservation, clé
// d'idempotence, moyen et montant remis. Aucune donnée sensible.
// Cycle de vie :
//   création   → à l'envoi du POST /payments, avant la réponse ;
//   reprise    → réessai avec la MÊME clé et les MÊMES paramètres ;
//   succès     → nettoyage ;
//   rejet 4xx  → rejet certain : la clé est libérée, nouvelle tranche ;
//   échec ambigu (réseau, 5xx) → conservée, paramètres figés.
//
// Un encaissement espèces a pu être physiquement pris au guichet alors
// que la réponse s'est perdue : on ne remplace jamais une clé encore
// incertaine et on ne démarre pas de nouvelle vente avant résolution.

export type PosPaymentMethod = "cb" | "cash" | "ancv" | "check";

export type PosPendingPayment = {
  reservationId: string;
  key: string;
  method: PosPaymentMethod;
  amount: string;
};

const POS_STORAGE_KEY = "musee:pos-pending-payment";

export function readPosPending(
  storage: Pick<Storage, "getItem">
): PosPendingPayment | null {
  try {
    const raw = storage.getItem(POS_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PosPendingPayment;
    if (!parsed.reservationId || !parsed.key || !parsed.amount)
      return null;
    return parsed;
  } catch {
    return null;
  }
}

export function savePosPending(
  pending: PosPendingPayment,
  storage: Pick<Storage, "setItem">
): void {
  try {
    storage.setItem(POS_STORAGE_KEY, JSON.stringify(pending));
  } catch {
    // Quota/mode privé : la reprise en mémoire suffit pour la session.
  }
}

export function clearPosPending(
  storage: Pick<Storage, "removeItem">
): void {
  try {
    storage.removeItem(POS_STORAGE_KEY);
  } catch {
    // noop
  }
}

// Décision après GET /reservations/{id} de l'opération restaurée.
export type PosResolution =
  | "resume" // pending : reprendre le même encaissement (même clé)
  | "conclude" // confirmed : le paiement avait abouti — restaurer la vente
  | "abandon" // cancelled/expired : commande morte, clé libérable
  | "blocked"; // lecture impossible : état non établi — caisse figée

export function posResolution(
  httpOk: boolean,
  status: string | null
): PosResolution {
  // Un échec de lecture (réseau, 4xx/5xx, corps sans statut) ne prouve
  // rien sur l'encaissement : la caisse reste bloquée, aucune nouvelle
  // vente ne démarre silencieusement.
  if (!httpOk || status === null) return "blocked";
  if (status === "confirmed") return "conclude";
  if (status === "pending") return "resume";
  return "abandon";
}
