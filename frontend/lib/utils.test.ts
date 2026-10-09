import { describe, expect, it } from "vitest";

import { apiErrorDetail } from "./utils";

describe("apiErrorDetail", () => {
  it("renvoie le detail texte des erreurs métier (400/409)", () => {
    expect(
      apiErrorDetail({ detail: "Capacité insuffisante" }, "Erreur"),
    ).toBe("Capacité insuffisante");
  });

  it("aplatit le tableau d'erreurs Pydantic des 422", () => {
    const body = {
      detail: [
        {
          type: "less_than_equal",
          loc: ["body", "items", 0, "group_size"],
          msg: "Input should be less than or equal to 120",
          input: 121,
        },
      ],
    };
    expect(apiErrorDetail(body, "Erreur")).toBe(
      "items.0.group_size : Input should be less than or equal to 120",
    );
  });

  it("concatène plusieurs erreurs de validation", () => {
    const body = {
      detail: [
        { loc: ["body", "items"], msg: "List should have at most 120 items" },
        { loc: ["body", "amount"], msg: "Input should be greater than 0" },
      ],
    };
    expect(apiErrorDetail(body, "Erreur")).toBe(
      "items : List should have at most 120 items — " +
        "amount : Input should be greater than 0",
    );
  });

  it("ignore le préfixe loc « body » et les entrées sans message", () => {
    const body = {
      detail: [{ loc: ["body"], msg: "Body malformed" }, "inattendu", 42],
    };
    expect(apiErrorDetail(body, "Erreur")).toBe("Body malformed");
  });

  it("retombe sur le fallback si le tableau est vide ou illisible", () => {
    expect(apiErrorDetail({ detail: [] }, "Erreur")).toBe("Erreur");
    expect(apiErrorDetail({ detail: [{}] }, "Erreur")).toBe("Erreur");
  });

  it("retombe sur le fallback pour un detail non-texte/non-tableau", () => {
    expect(apiErrorDetail({ detail: { code: "x" } }, "Erreur")).toBe("Erreur");
    expect(apiErrorDetail({}, "Erreur")).toBe("Erreur");
    expect(apiErrorDetail(null, "Erreur")).toBe("Erreur");
  });
});
