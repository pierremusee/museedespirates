// Cycle de vie de la reprise de paiement web (pending-payment.ts) :
// persistance sessionStorage, décision resume/check/fresh, résolution
// de l'ancienne opération. Composant React hors scope (pas de tests
// de composants) — la logique testable est extraite ici.

import { describe, expect, it } from "vitest";

import {
  checkPreviousOutcome,
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

  it("sessionStorage indisponible → lecture null, écriture ignorée", () => {
    // Mode privé/quota : aucune donnée persistée, aucun crash — la
    // reprise en mémoire suffit pour la session (comportement dégradé).
    const broken = {
      getItem: () => {
        throw new DOMException("denied");
      },
      setItem: () => {
        throw new DOMException("denied");
      },
      removeItem: () => {
        throw new DOMException("denied");
      },
    };
    expect(readPendingPayment(broken)).toBeNull();
    expect(() => savePendingPayment(PENDING, broken)).not.toThrow();
    expect(() => clearPendingPayment(broken)).not.toThrow();
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

  it.each(["processing", "", "statut inconnu", "CONFIRMED"])(
    "statut non reconnu %j → unknown, jamais abandon",
    (status) => {
      // Liste blanche : toute valeur non prévue (nouvel état backend,
      // chaîne mal formée) conserve l'opération et bloque le flux.
      expect(previousOutcome(true, status)).toBe("unknown");
    }
  );
});

// Flux complet « GET + décision » avec fetch injecté : vérifie que les
// pannes réelles (réseau, 5xx, corps mal formé) aboutissent à « unknown »
// — la branche qui conserve l'opération et bloque tout nouvel achat.
describe("checkPreviousOutcome (fetch injecté)", () => {
  const API = "http://api.test";

  const fetchOk =
    (body: unknown): typeof fetch =>
    async () =>
      ({ ok: true, json: async () => body }) as Response;

  it("GET 200 confirmé → conclude", async () => {
    expect(
      await checkPreviousOutcome(fetchOk({ status: "confirmed" }), API, PENDING)
    ).toBe("conclude");
  });

  it("GET 200 pending → abandon", async () => {
    expect(
      await checkPreviousOutcome(fetchOk({ status: "pending" }), API, PENDING)
    ).toBe("abandon");
  });

  it("GET 500 → unknown", async () => {
    const f: typeof fetch = async () =>
      ({ ok: false, json: async () => ({}) }) as Response;
    expect(await checkPreviousOutcome(f, API, PENDING)).toBe("unknown");
  });

  it("erreur réseau (fetch jette) → unknown", async () => {
    const f: typeof fetch = async () => {
      throw new TypeError("fetch failed");
    };
    expect(await checkPreviousOutcome(f, API, PENDING)).toBe("unknown");
  });

  it("corps JSON mal formé → unknown", async () => {
    const f: typeof fetch = async () =>
      ({
        ok: true,
        json: async () => {
          throw new SyntaxError("Unexpected token");
        },
      }) as unknown as Response;
    expect(await checkPreviousOutcome(f, API, PENDING)).toBe("unknown");
  });

  it("statut non-string (ex. nombre) → unknown", async () => {
    expect(
      await checkPreviousOutcome(fetchOk({ status: 42 }), API, PENDING)
    ).toBe("unknown");
  });

  it("statut non reconnu → unknown", async () => {
    expect(
      await checkPreviousOutcome(fetchOk({ status: "processing" }), API, PENDING)
    ).toBe("unknown");
  });
});
