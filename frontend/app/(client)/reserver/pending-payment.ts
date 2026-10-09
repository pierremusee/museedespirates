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
export type PreviousOutcome =
  | "conclude" // confirmée : le paiement avait abouti
  | "abandon" // pending/cancelled/expired : commande morte, clé libérable
  | "unknown"; // lecture impossible : état non établi, ne rien décider

export function previousOutcome(
  httpOk: boolean,
  status: string | null
): PreviousOutcome {
  // Un échec de lecture (réseau, 4xx/5xx, corps sans statut) ou un
  // statut non reconnu (valeur nouvelle/inattendue du backend) ne
  // prouve rien sur le sort du paiement : l'opération reste incertaine
  // et doit être conservée — jamais assimilée à un abandon.
  if (!httpOk || status === null) return "unknown";
  if (status === "confirmed") return "conclude";
  // Abandon = décision purement locale (oubli de la clé + nouvelle
  // opération autorisée) — rien n'est supprimé côté serveur : un
  // éventuel encaissement reste tracé sur la réservation. Sûr ici car
  // le canal web n'admet que le paiement CB intégral : une réservation
  // « pending » ne peut donc pas porter de paiement partiel.
  if (["pending", "cancelled", "expired"].includes(status))
    return "abandon";
  return "unknown";
}

// Vérification d'une opération précédente (autre produit) : lecture
// GET + décision. Extraite pour être testable avec un fetch injecté —
// les effets de bord (storage, navigation) restent dans le composant.
export async function checkPreviousOutcome(
  apiFetch: typeof fetch,
  apiUrl: string,
  pending: PendingPayment
): Promise<PreviousOutcome> {
  let httpOk = false;
  let status: string | null = null;
  try {
    const res = await apiFetch(
      `${apiUrl}/reservations/${pending.reservationId}`,
      { cache: "no-store" }
    );
    httpOk = res.ok;
    if (res.ok) {
      const body = (await res.json().catch(() => null)) as {
        status?: unknown;
      } | null;
      status = typeof body?.status === "string" ? body.status : null;
    }
  } catch {
    // Injoignable → unknown
  }
  return previousOutcome(httpOk, status);
}
