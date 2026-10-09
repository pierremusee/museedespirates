// Cycle de vie de la reprise de paiement web (pending-payment.ts) :
// persistance sessionStorage, décision resume/check/fresh, résolution
// de l'ancienne opération. Composant React hors scope (pas de tests
// de composants) — la logique testable est extraite ici.

import { describe, expect, it } from "vitest";

import {
  clearPendingPayment,
  pendingAction,
  previousOutcome,
  readPendingPayment,
  savePendingPayment,
  type PendingPayment,
} from "./pending-payment";

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  };
}

const PENDING: PendingPayment = {
  reservationId: "resa-1",
  productCode: "museum_entry",
  paymentKey: "key-1",
  amount: "12.00",
};

describe("pending-payment storage", () => {
  it("roundtrip écriture/lecture/nettoyage", () => {
    const s = fakeStorage();
    expect(readPendingPayment(s)).toBeNull();
    savePendingPayment(PENDING, s);
    expect(readPendingPayment(s)).toEqual(PENDING);
    clearPendingPayment(s);
    expect(readPendingPayment(s)).toBeNull();
  });

  it("JSON corrompu ou incomplet → null", () => {
    expect(readPendingPayment(fakeStorage({ "musee:pending-payment": "{oops" }))).toBeNull();
    expect(
      readPendingPayment(
        fakeStorage({ "musee:pending-payment": '{"amount":"5.00"}' })
      )
    ).toBeNull();
  });
});

describe("pendingAction", () => {
  it("aucune opération → fresh", () => {
    expect(pendingAction(null, "museum_entry")).toBe("fresh");
  });

  it("même produit → resume (rejoue la même clé)", () => {
    expect(pendingAction(PENDING, "museum_entry")).toBe("resume");
  });

  it("autre produit → check_previous (résoudre avant d'écraser)", () => {
    expect(pendingAction(PENDING, "pass_1_show")).toBe("check_previous");
  });
});

describe("previousOutcome", () => {
  it("confirmée → conclude (le paiement avait abouti)", () => {
    expect(previousOutcome(true, "confirmed")).toBe("conclude");
  });

  it.each(["pending", "cancelled", "expired"])(
    "%s → abandon (commande morte, nouvelle opération libre)",
    (status) => {
      expect(previousOutcome(true, status)).toBe("abandon");
    }
  );

  it("GET en échec (réseau, 4xx, 5xx) → unknown, opération conservée", () => {
    // Une erreur de lecture ne prouve pas l'échec du paiement :
    // l'opération reste incertaine et bloque tout nouvel achat.
    expect(previousOutcome(false, null)).toBe("unknown");
    expect(previousOutcome(false, "pending")).toBe("unknown");
  });

  it("réponse OK sans statut → unknown (état non établi)", () => {
    expect(previousOutcome(true, null)).toBe("unknown");
  });
});
