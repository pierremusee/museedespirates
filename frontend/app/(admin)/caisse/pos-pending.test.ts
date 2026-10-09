// Cycle de vie de la reprise d'encaissement POS (pos-pending.ts) :
// persistance sessionStorage et décision de résolution après GET.
// Les décisions de contrôle du flux sont testées directement — elles
// portent les garanties « jamais de clé perdue, jamais de nouvelle
// vente silencieuse ».

import { describe, expect, it } from "vitest";

import {
  clearPosPending,
  posResolution,
  readPosPending,
  resolveStoredPending,
  savePosPending,
  type PosPendingPayment,
} from "./pos-pending";

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  };
}

const PENDING: PosPendingPayment = {
  reservationId: "resa-9",
  key: "key-9",
  method: "cash",
  amount: "5.00",
};

describe("pos-pending storage", () => {
  it("roundtrip écriture/lecture/nettoyage", () => {
    const s = fakeStorage();
    expect(readPosPending(s)).toBeNull();
    savePosPending(PENDING, s);
    expect(readPosPending(s)).toEqual(PENDING);
    clearPosPending(s);
    expect(readPosPending(s)).toBeNull();
  });

  it("JSON corrompu ou incomplet → null", () => {
    expect(
      readPosPending(fakeStorage({ "musee:pos-pending-payment": "{oops" }))
    ).toBeNull();
    expect(
      readPosPending(
        fakeStorage({ "musee:pos-pending-payment": '{"amount":"5.00"}' })
      )
    ).toBeNull();
  });

  it("sessionStorage indisponible → lecture null, écriture ignorée", () => {
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
    expect(readPosPending(broken)).toBeNull();
    expect(() => savePosPending(PENDING, broken)).not.toThrow();
    expect(() => clearPosPending(broken)).not.toThrow();
  });
});

describe("posResolution", () => {
  it("commande pending → resume : même clé, mêmes paramètres", () => {
    expect(posResolution(true, "pending")).toBe("resume");
  });

  it("commande confirmée → conclude : le paiement avait abouti", () => {
    expect(posResolution(true, "confirmed")).toBe("conclude");
  });

  it.each(["cancelled", "expired"])(
    "commande %s → abandon : commande morte, clé libérable",
    (status) => {
      expect(posResolution(true, status)).toBe("abandon");
    }
  );

  it("GET en échec (réseau, 4xx, 5xx) → blocked : rien n'est décidé", () => {
    // Un échec de lecture ne prouve pas l'échec de l'encaissement :
    // la caisse reste figée, aucune nouvelle vente silencieuse.
    expect(posResolution(false, null)).toBe("blocked");
    expect(posResolution(false, "pending")).toBe("blocked");
  });

  it("réponse OK sans statut → blocked (état non établi)", () => {
    expect(posResolution(true, null)).toBe("blocked");
  });

  it.each(["processing", "", "statut inconnu", "PENDING"])(
    "statut non reconnu %j → blocked, jamais abandon ni déblocage",
    (status) => {
      // Liste blanche : toute valeur non prévue (nouvel état backend,
      // chaîne mal formée) conserve l'opération et garde la caisse
      // bloquée jusqu'à résolution.
      expect(posResolution(true, status)).toBe("blocked");
    }
  );
});

// Flux complet « GET + décision » au rechargement avec fetch injecté :
// vérifie que les pannes réelles aboutissent à « blocked » — la caisse
// reste figée, l'opération persistée n'est jamais supprimée.
describe("resolveStoredPending (fetch injecté)", () => {
  const API = "http://api.test";

  const fetchOk =
    (body: unknown): typeof fetch =>
    async () =>
      ({ ok: true, json: async () => body }) as Response;

  it("GET 200 pending → resume + détail restitué", async () => {
    const r = await resolveStoredPending(
      fetchOk({ status: "pending", payments: [] }),
      API,
      PENDING
    );
    expect(r.action).toBe("resume");
    expect(r.detail?.status).toBe("pending");
  });

  it("GET 200 confirmed → conclude", async () => {
    const r = await resolveStoredPending(
      fetchOk({ status: "confirmed" }),
      API,
      PENDING
    );
    expect(r.action).toBe("conclude");
  });

  it("GET 500 → blocked", async () => {
    const f: typeof fetch = async () =>
      ({ ok: false, json: async () => ({}) }) as Response;
    const r = await resolveStoredPending(f, API, PENDING);
    expect(r.action).toBe("blocked");
    expect(r.detail).toBeNull();
  });

  it("erreur réseau (fetch jette) → blocked", async () => {
    const f: typeof fetch = async () => {
      throw new TypeError("fetch failed");
    };
    expect((await resolveStoredPending(f, API, PENDING)).action).toBe(
      "blocked"
    );
  });

  it("corps JSON mal formé → blocked", async () => {
    const f: typeof fetch = async () =>
      ({
        ok: true,
        json: async () => {
          throw new SyntaxError("Unexpected token");
        },
      }) as unknown as Response;
    expect((await resolveStoredPending(f, API, PENDING)).action).toBe(
      "blocked"
    );
  });

  it("statut non-string (ex. nombre) → blocked", async () => {
    const r = await resolveStoredPending(
      fetchOk({ status: 42 }),
      API,
      PENDING
    );
    expect(r.action).toBe("blocked");
  });

  it("statut non reconnu → blocked", async () => {
    const r = await resolveStoredPending(
      fetchOk({ status: "processing" }),
      API,
      PENDING
    );
    expect(r.action).toBe("blocked");
  });
});
