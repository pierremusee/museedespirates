import { describe, expect, it } from "vitest";

import {
  absorbIntoPass,
  addProductToCart,
  addonAccessError,
  addonCoverageError,
  copiableCounts,
  coveredSessionIds,
  lineEstimate,
  passSuggestion,
  pmrLineError,
  strictPassHint,
  zeroCounts,
  type CartLine,
  type Product,
  type SessionOption,
  type Tariff,
} from "./cart";

// Catalogue calqué sur backend/scripts/seed_db.py (grille basse saison).
const MUSEUM: Product = {
  code: "museum_entry",
  label: "Entrée Musée",
  kind: "simple",
  price_adult: "12.00",
  price_child: "8.00",
  price_reduced: "9.00",
  family_base_price: null,
  extra_child_price: null,
  is_addon: false,
  components: [{ component_type: "museum_day", quantity: 1 }],
};

const SHOW: Product = {
  code: "theater_show",
  label: "Billet Théâtre — 1 séance",
  kind: "simple",
  price_adult: "10.00",
  price_child: "7.00",
  price_reduced: "8.00",
  family_base_price: null,
  extra_child_price: null,
  is_addon: false,
  components: [{ component_type: "theater_session", quantity: 1 }],
};

const EXTRA_SHOW: Product = {
  code: "extra_show",
  label: "Séance supplémentaire (tarif réduit)",
  kind: "simple",
  price_adult: "5.00",
  price_child: "3.50",
  price_reduced: "4.00",
  family_base_price: null,
  extra_child_price: null,
  is_addon: true,
  components: [{ component_type: "theater_session", quantity: 1 }],
};

const PASS: Product = {
  code: "pass_1_show",
  label: "Pass 1 Spectacle (Musée + Théâtre)",
  kind: "pass",
  price_adult: "20.00",
  price_child: "13.00",
  price_reduced: "15.00",
  family_base_price: null,
  extra_child_price: null,
  is_addon: false,
  components: [
    { component_type: "museum_day", quantity: 1 },
    { component_type: "theater_session", quantity: 1 },
  ],
};

const PASS_2: Product = {
  code: "pass_2_shows",
  label: "Pass 2 Spectacles (Musée + 2 séances)",
  kind: "pass",
  price_adult: "24.00",
  price_child: "16.00",
  price_reduced: "18.00",
  family_base_price: null,
  extra_child_price: null,
  is_addon: false,
  components: [
    { component_type: "museum_day", quantity: 1 },
    { component_type: "theater_session", quantity: 2 },
  ],
};

const FAMILY_MUSEUM: Product = {
  code: "family_museum",
  label: "Forfait Famille — Musée seul (2A + 2E)",
  kind: "family",
  price_adult: null,
  price_child: null,
  price_reduced: null,
  family_base_price: "35.00",
  extra_child_price: "6.00",
  is_addon: false,
  components: [{ component_type: "museum_day", quantity: 1 }],
};

const PRODUCTS = [MUSEUM, SHOW, EXTRA_SHOW, PASS, PASS_2, FAMILY_MUSEUM];

const SESSIONS: SessionOption[] = [
  { id: "s1", label: "14:00", show: "Le Kraken", remaining: 50, expired: false },
  { id: "s2", label: "19:00", show: "Le Kraken", remaining: 50, expired: false },
];

function counts(patch: Partial<Record<Tariff, number>> = {}) {
  return { ...zeroCounts(), ...patch };
}

function line(
  id: number,
  product: Product,
  c: Record<Tariff, number>,
  sessionIds: string[] = []
): CartLine {
  return {
    id,
    product,
    counts: c,
    groupSize: 8,
    extraChildren: 0,
    sessionIds,
  };
}

describe("conversion automatique Musée + Théâtre → Pass 1 Spectacle", () => {
  it("convertit quand les compositions sont strictement identiques", () => {
    let r = addProductToCart([], MUSEUM, PRODUCTS, SESSIONS, 1);
    expect(r.optimized).toBeNull();
    expect(r.lines[0].product.code).toBe("museum_entry");

    r = addProductToCart(r.lines, SHOW, PRODUCTS, SESSIONS, r.nextId);
    expect(r.optimized?.persons).toBe(1);
    expect(r.optimized?.savings).toBeCloseTo(2); // 12 + 10 − 20
    expect(r.absorbed).toBe(false);

    // Les lignes consommées disparaissent : jamais musée + pass ensemble.
    expect(r.lines).toHaveLength(1);
    const passLine = r.lines[0];
    expect(passLine.product.code).toBe("pass_1_show");
    expect(passLine.counts.adult).toBe(1);
    expect(passLine.optimized).toBe(true);
    expect(passLine.sessionIds).toEqual(["s1"]);
    expect(lineEstimate(passLine, false)).toBe(20);
  });

  it("fonctionne aussi dans l'ordre Théâtre puis Musée", () => {
    let r = addProductToCart([], SHOW, PRODUCTS, SESSIONS, 1);
    r = addProductToCart(r.lines, MUSEUM, PRODUCTS, SESSIONS, r.nextId);
    expect(r.optimized).not.toBeNull();
    expect(r.lines.map((l) => l.product.code)).toEqual(["pass_1_show"]);
  });

  it("plusieurs personnes : toute la composition bascule sur le pass", () => {
    let r = addProductToCart([], MUSEUM, PRODUCTS, SESSIONS, 1);
    r = addProductToCart(r.lines, MUSEUM, PRODUCTS, SESSIONS, r.nextId); // 2A
    r = addProductToCart(r.lines, SHOW, PRODUCTS, SESSIONS, r.nextId);
    expect(r.lines).toHaveLength(1);
    expect(r.lines[0].product.code).toBe("pass_1_show");
    expect(r.lines[0].counts.adult).toBe(2);
    expect(r.optimized?.persons).toBe(2);
    expect(r.optimized?.savings).toBeCloseTo(4); // (12+10−20)×2
  });

  it("couvre les tarifs mixtes (adulte + enfant + profil gratuit)", () => {
    const comp = counts({ adult: 1, child: 2, under_4: 1 });
    const ls = [
      line(1, MUSEUM, comp),
      line(2, SHOW, comp, ["s2"]),
    ];
    const hint = strictPassHint(ls, PRODUCTS);
    expect(hint?.persons).toBe(4);
    expect(hint?.sessionId).toBe("s2");
    const next = absorbIntoPass(ls, hint!, PASS, 3);
    expect(next).toHaveLength(1);
    expect(next[0].counts).toEqual(comp);
  });

  it("ne fusionne pas des compositions différentes (personnes distinctes)", () => {
    // 1 adulte au musée, 1 enfant au théâtre : achats de personnes
    // différentes — ni conversion auto ni suggestion.
    const ls = [
      line(1, MUSEUM, counts({ adult: 1 })),
      line(2, SHOW, counts({ child: 1 }), ["s1"]),
    ];
    expect(strictPassHint(ls, PRODUCTS)).toBeNull();
    expect(passSuggestion(ls, PRODUCTS)).toBeNull();
  });

  it("ignore un panier 100 % gratuit (aucune économie)", () => {
    const ls = [
      line(1, MUSEUM, counts({ under_4: 1 })),
      line(2, SHOW, counts({ under_4: 1 }), ["s1"]),
    ];
    expect(strictPassHint(ls, PRODUCTS)).toBeNull();
  });
});

describe("conversion partielle — suggestion explicite conservée", () => {
  const partial = () => [
    line(1, MUSEUM, counts({ adult: 2 })),
    line(2, SHOW, counts({ adult: 1 }), ["s1"]),
  ];

  it("pas de conversion automatique sur composition asymétrique", () => {
    // L'agent renchérit le musée : 3A musée vs 1A théâtre reste ambigu.
    const r = addProductToCart(partial(), MUSEUM, PRODUCTS, SESSIONS, 3);
    expect(r.optimized).toBeNull();
    expect(
      r.lines.find((l) => l.product.code === "museum_entry")?.counts.adult
    ).toBe(3);
    // Mais la suggestion manuelle reste disponible pour 1 personne.
    const hint = passSuggestion(r.lines, PRODUCTS);
    expect(hint?.persons).toBe(1);
  });

  it("la conversion manuelle conserve le reliquat musée", () => {
    const hint = passSuggestion(partial(), PRODUCTS)!;
    const next = absorbIntoPass(partial(), hint, PASS, 3);
    expect(next).toHaveLength(2);
    expect(
      next.find((l) => l.product.code === "museum_entry")?.counts.adult
    ).toBe(1);
    expect(
      next.find((l) => l.product.code === "pass_1_show")?.counts.adult
    ).toBe(1);
  });
});

describe("ajout direct du Pass", () => {
  it("absorbe les lignes simples correspondantes déjà au panier", () => {
    const ls = [
      line(1, MUSEUM, counts({ adult: 1 })),
      line(2, SHOW, counts({ adult: 1 }), ["s1"]),
    ];
    const r = addProductToCart(ls, PASS, PRODUCTS, SESSIONS, 3);
    expect(r.absorbed).toBe(true);
    expect(r.lines).toHaveLength(1);
    expect(r.lines[0].product.code).toBe("pass_1_show");
    expect(r.lines[0].sessionIds).toEqual(["s1"]);
  });

  it("s'ajoute comme ligne normale sans lignes à absorber", () => {
    const r = addProductToCart([], PASS, PRODUCTS, SESSIONS, 1);
    expect(r.absorbed).toBe(false);
    expect(r.optimized).toBeNull();
    expect(r.lines).toHaveLength(1);
    expect(r.lines[0].product.code).toBe("pass_1_show");
    expect(r.lines[0].counts.adult).toBe(1);
  });

  it("conserve les lignes non concernées (forfait famille)", () => {
    const ls = [
      line(1, FAMILY_MUSEUM, zeroCounts()),
      line(2, MUSEUM, counts({ adult: 1 })),
      line(3, SHOW, counts({ adult: 1 }), ["s1"]),
    ];
    const r = addProductToCart(ls, PASS, PRODUCTS, SESSIONS, 4);
    expect(r.absorbed).toBe(true);
    expect(r.lines).toHaveLength(2);
    expect(r.lines.some((l) => l.product.code === "family_museum")).toBe(true);
  });
});

describe("panier après optimisation", () => {
  function optimizedCart() {
    const first = addProductToCart([], MUSEUM, PRODUCTS, SESSIONS, 1);
    return addProductToCart(first.lines, SHOW, PRODUCTS, SESSIONS, first.nextId);
  }

  it("la ligne pass reste modifiable et le total se recalcule", () => {
    const r = optimizedCart();
    const bumped = r.lines.map((l) => ({
      ...l,
      counts: { ...l.counts, adult: l.counts.adult + 1 },
    }));
    expect(bumped[0].counts.adult).toBe(2);
    expect(bumped[0].optimized).toBe(true);
    const total = bumped.reduce((s, l) => s + lineEstimate(l, false), 0);
    expect(total).toBe(40);
  });

  it("la suppression de la ligne vide le panier sans relique", () => {
    const r = optimizedCart();
    const remaining = r.lines.filter((l) => l.id !== r.lines[0].id);
    expect(remaining).toHaveLength(0);
  });
});

describe("miroir de composition à l'ajout", () => {
  it("ne recopie pas les droits déjà couverts par un pass au panier", () => {
    const ls = [line(1, PASS, counts({ adult: 2, child: 1 }), ["s1"])];
    // Ajouter un billet théâtre pour un nouveau visiteur ne doit pas
    // recompter les 3 personnes déjà couvertes par le pass.
    const c = copiableCounts(ls, SHOW);
    expect(c.adult).toBe(1);
    expect(c.child).toBe(0);
  });

  it("recopie les lignes musée lors de l'ajout d'un billet théâtre", () => {
    const ls = [line(1, MUSEUM, counts({ adult: 2, child: 1 }))];
    const c = copiableCounts(ls, SHOW);
    expect(c.adult).toBe(2);
    expect(c.child).toBe(1);
  });
});

describe("règle PMR (miroir du moteur métier)", () => {
  const VISIT = "2026-11-01";

  it("accepte 1 porteur + 1 accompagnateur sur la même séance", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 1 }), ["s1"]),
      line(2, SHOW, counts({ pmr_companion: 1 }), ["s1"]),
    ];
    expect(pmrLineError(ls[1], ls, VISIT)).toBeNull();
  });

  it("accepte 2 porteurs + 2 accompagnateurs", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 2, pmr_companion: 2 }), ["s1"]),
    ];
    expect(pmrLineError(ls[0], ls, VISIT)).toBeNull();
  });

  it("rejette un accompagnateur sans porteur", () => {
    const ls = [line(1, SHOW, counts({ pmr_companion: 1 }), ["s1"])];
    expect(pmrLineError(ls[0], ls, VISIT)).toMatch(/invalidité/);
  });

  it("rejette 2 accompagnateurs pour 1 porteur même co-présents", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 1, pmr_companion: 2 }), ["s1"]),
    ];
    expect(pmrLineError(ls[0], ls, VISIT)).toMatch(/invalidité/);
  });

  it("le ratio est agrégé panier : porteur et accompagnateur sur des lignes différentes", () => {
    // L'ancienne garde par ligne rejetait un pmr sans disability sur la
    // même ligne — la règle métier est un ratio agrégé + co-présence.
    const ls = [
      line(1, PASS, counts({ disability: 1 }), ["s1"]),
      line(2, SHOW, counts({ pmr_companion: 1 }), ["s1"]),
    ];
    expect(pmrLineError(ls[1], ls, VISIT)).toBeNull();
  });

  it("rejette un accompagnateur sur une séance sans porteur", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 1 }), ["s1"]),
      line(2, SHOW, counts({ pmr_companion: 1 }), ["s2"]),
    ];
    expect(pmrLineError(ls[1], ls, VISIT)).toMatch(/partager/);
  });

  it("la visite musée du même jour compte comme co-présence", () => {
    const ls = [
      line(1, MUSEUM, counts({ disability: 1 })),
      line(2, MUSEUM, counts({ pmr_companion: 1 })),
    ];
    expect(pmrLineError(ls[1], ls, VISIT)).toBeNull();
  });

  it("un add-on accompagnateur n'est pas une personne de plus", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 1, pmr_companion: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ disability: 1, pmr_companion: 1 }), ["s2"]),
    ];
    expect(pmrLineError(ls[0], ls, VISIT)).toBeNull();
    // L'add-on accompagnateur reste valide : son accès (s2) est aussi
    // détenu par le porteur via son propre add-on.
    expect(pmrLineError(ls[1], ls, VISIT)).toBeNull();
  });

  it("rejette un add-on accompagnateur sur une séance sans porteur", () => {
    // A1 : la co-présence « un point commun » laissait l'accompagnateur
    // emporter une séance supplémentaire gratuite sans porteur.
    const ls = [
      line(1, SHOW, counts({ disability: 1 }), ["s1"]),
      line(2, SHOW, counts({ pmr_companion: 1 }), ["s1"]),
      line(3, EXTRA_SHOW, counts({ pmr_companion: 1 }), ["s2"]),
    ];
    expect(pmrLineError(ls[2], ls, VISIT)).toMatch(/partager/);
    expect(pmrLineError(ls[1], ls, VISIT)).toBeNull();
  });

  it("la co-présence via l'add-on ne couvre pas les autres accès", () => {
    // La séance partagée vient de l'add-on, mais la ligne de base de
    // l'accompagnateur vise s2 — aucun porteur sur s2.
    const ls = [
      line(1, SHOW, counts({ disability: 1 }), ["s1"]),
      line(2, SHOW, counts({ pmr_companion: 1 }), ["s2"]),
      line(3, EXTRA_SHOW, counts({ pmr_companion: 1 }), ["s1"]),
    ];
    expect(pmrLineError(ls[1], ls, VISIT)).toMatch(/partager/);
  });

  it("un add-on disability ne crée pas de porteur", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ disability: 1 }), ["s2"]),
      line(3, SHOW, counts({ pmr_companion: 2 }), ["s1"]),
    ];
    expect(pmrLineError(ls[2], ls, VISIT)).toMatch(/invalidité/);
  });
});

describe("add-ons — couverture et rattachement (miroir du moteur métier)", () => {
  const VISIT = "2026-11-01";

  it("add-on couvert par une personne de base du même droit : accepté", () => {
    const ls = [
      line(1, SHOW, counts({ adult: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), ["s2"]),
    ];
    expect(addonCoverageError(ls[1], ls)).toBeNull();
    expect(addonAccessError(ls[1], ls, VISIT)).toBeNull();
  });

  it("add-on sans base du même droit : rejeté", () => {
    const ls = [
      line(1, MUSEUM, counts({ adult: 1 })),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), ["s2"]),
    ];
    expect(addonCoverageError(ls[1], ls)).not.toBeNull();
  });

  it("add-on gratuit non couvert par une base payante : rejeté", () => {
    // Catégorie ET profil comptent : un adulte payant ne couvre pas un
    // supplément d'un accompagnateur PMR gratuit.
    const ls = [
      line(1, SHOW, counts({ adult: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ pmr_companion: 1 }), ["s2"]),
    ];
    expect(addonCoverageError(ls[1], ls)).not.toBeNull();
  });

  it("add-on sur la même séance que la base : refusé (doublon)", () => {
    const ls = [
      line(1, SHOW, counts({ adult: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), ["s1"]),
    ];
    expect(addonCoverageError(ls[1], ls)).toBeNull(); // droit couvert…
    expect(addonAccessError(ls[1], ls, VISIT)).not.toBeNull(); // …mais déjà détenu
  });

  it("add-on sur une séance déjà incluse dans le pass : refusé", () => {
    const ls = [
      line(1, PASS, counts({ adult: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), ["s1"]),
    ];
    expect(addonAccessError(ls[1], ls, VISIT)).not.toBeNull();
  });

  it("deux personnes sur s1 + add-on s1 : refusé pour toutes les personnes", () => {
    const ls = [
      line(1, SHOW, counts({ adult: 2 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), ["s1"]),
    ];
    expect(addonAccessError(ls[1], ls, VISIT)).not.toBeNull();
  });

  it("même séance pour une AUTRE personne du groupe : accepté", () => {
    // A (pass : musée + s1) et B (musée seul) — le supplément s1
    // complète les droits de B : 2 personnes distinctes sur s1.
    const ls = [
      line(1, PASS, counts({ adult: 1 }), ["s1"]),
      line(2, MUSEUM, counts({ adult: 1 })),
      line(3, EXTRA_SHOW, counts({ adult: 1 }), ["s1"]),
    ];
    expect(addonAccessError(ls[2], ls, VISIT)).toBeNull();
  });

  it("add-on PMR sur la même séance (B1) : refusé", () => {
    const ls = [
      line(1, SHOW, counts({ disability: 1, pmr_companion: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ pmr_companion: 1 }), ["s1"]),
    ];
    expect(addonCoverageError(ls[1], ls)).toBeNull();
    expect(addonAccessError(ls[1], ls, VISIT)).not.toBeNull();
  });

  it("coveredSessionIds grise les séances déjà couvertes", () => {
    const base = [
      line(1, SHOW, counts({ adult: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), ["s2"]),
    ];
    expect([...coveredSessionIds(base[1], base, VISIT)]).toEqual(["s1"]);
    // Pass musée + s1 : seule s1 est couverte, s2 reste choisissable.
    const passCart = [
      line(1, PASS, counts({ adult: 1 }), ["s1"]),
      line(2, EXTRA_SHOW, counts({ adult: 1 }), [""]),
    ];
    expect([...coveredSessionIds(passCart[1], passCart, VISIT)]).toEqual([
      "s1",
    ]);
  });
});
